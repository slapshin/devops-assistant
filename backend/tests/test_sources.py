"""Multi-source analyses (T014): sources are merged, and one failing source is disclosed."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.providers import FakeExplanationProvider
from app.analysis.engine import RobustDetector
from app.domain.common import SOURCE_FAMILIES, Scope, SourceKind
from app.domain.detector_config import DetectorConfig
from app.domain.findings import SignalStatus
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    OpenedSource,
    ProgressReporter,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageProgress
from app.domain.metrics import MetricCapability
from app.domain.report import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisWindows,
    ReportState,
    SourceInfo,
)
from app.service import AnalysisPipeline
from app.sources.prometheus.promql import ScopeViolation, scope_matchers
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from tests.helpers import StaticSources, make_scope

END = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class FailingSource:
    def __init__(self, during: str) -> None:
        self.during = during

    def _fail(self, stage: str) -> None:
        if stage == self.during:
            raise SourceError(SourceErrorKind.AUTH, "token rejected")

    async def source_info(self) -> SourceInfo:
        self._fail("info")
        return SourceInfo(base_url="https://failing.example")

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        self._fail("capabilities")
        return []

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult:
        self._fail("collect")
        return CollectionResult(series=[], exclusions=[])


class Progress:
    async def update(self, progress: StageProgress) -> None:
        pass


def _pipeline(*sources: OpenedSource) -> tuple[AnalysisPipeline, AnalysisRequest]:
    config = DetectorConfig()
    pipeline = AnalysisPipeline(
        StaticSources(*sources), RobustDetector(), config, FakeExplanationProvider()
    )
    request = AnalysisRequest(
        scope=make_scope(),
        end_time=END,
        detector_version=config.version,
        config_hash=config.config_hash,
    )
    return pipeline, request


def _opened(source: object) -> OpenedSource:
    return OpenedSource(SourceKind.PROMETHEUS, source)  # type: ignore[arg-type]


@pytest.mark.parametrize("during", ["info", "capabilities", "collect"])
async def test_one_failing_source_makes_the_report_partial(during: str) -> None:
    pipeline, request = _pipeline(
        _opened(SyntheticMetricsSource("incident")), _opened(FailingSource(during))
    )
    report = await pipeline.run(
        "01999a2b-0000-7000-8000-00000000000b", request, Progress(), CancellationToken()
    )

    assert report.state is ReportState.PARTIAL
    assert report.findings, "the healthy source is still analysed"
    failed = [e for e in report.exclusions if e.code == "source_unavailable"]
    assert {e.family for e in failed} == set(SOURCE_FAMILIES[SourceKind.PROMETHEUS])
    assert all("token rejected" in e.message for e in failed)
    assert [s.kind for s in report.sources] == [SourceKind.PROMETHEUS] * (
        1 if during != "collect" else 2
    )
    assert report.source == report.sources[0]


async def test_all_sources_failing_fails_the_run() -> None:
    pipeline, request = _pipeline(_opened(FailingSource("capabilities")))
    with pytest.raises(SourceError, match="token rejected"):
        await pipeline.run(
            "01999a2b-0000-7000-8000-00000000000c", request, Progress(), CancellationToken()
        )


async def test_failed_source_families_report_source_error() -> None:
    pipeline, request = _pipeline(_opened(FailingSource("info")), _opened(FailingSource("none")))
    report = await pipeline.run(
        "01999a2b-0000-7000-8000-00000000000d", request, Progress(), CancellationToken()
    )
    assert {c.status for c in report.coverage} == {SignalStatus.SOURCE_ERROR}


def test_prometheus_refuses_an_empty_scope() -> None:
    with pytest.raises(ScopeViolation):
        scope_matchers(Scope(project_id="p", project_name="p", matchers=[]))


def test_schema_2_0_reports_still_load() -> None:
    raw = json.loads((FIXTURES / "reports" / "report_anomalies.json").read_text())
    raw["schema_version"] = "2.0"
    raw["source"] = {k: v for k, v in raw["source"].items() if k != "kind"}
    del raw["sources"]
    for cap in raw["capabilities"]:
        del cap["source"]

    report = AnalysisReport.model_validate(raw)
    assert report.sources == []
    assert [s.kind for s in report.all_sources] == [SourceKind.PROMETHEUS]
