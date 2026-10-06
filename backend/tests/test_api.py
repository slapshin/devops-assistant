"""API, job lifecycle, and persistence integration tests (real SQLite, synthetic source)."""

import asyncio
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.providers import FakeExplanationProvider
from app.analysis.engine import RobustDetector
from app.domain.common import Scope
from app.domain.detector_config import DetectorConfig
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    ProgressReporter,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageProgress
from app.domain.metrics import MetricCapability
from app.domain.report import AnalysisRequest, AnalysisWindows, Exclusion
from app.jobs import RunnerLimits
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from tests.helpers import StaticSources, make_scope

SHOP_MATCHERS = [{"name": "env", "value": "production"}, {"name": "project", "value": "shop"}]
SHOP_NAME = "shop / production"


class GatedSource(SyntheticMetricsSource):
    """Blocks collection until the test opens the gate (a thread-safe event)."""

    def __init__(self, scenario: str = "incident") -> None:
        super().__init__(scenario)
        self.gate = threading.Event()
        self.started = threading.Event()

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult:
        self.started.set()
        while not self.gate.is_set():
            cancel.raise_if_cancelled()
            await asyncio.sleep(0.01)
        return await super().collect(scope, windows, capabilities, progress, cancel)


class FlakySource(SyntheticMetricsSource):
    async def collect(self, *args: Any, **kwargs: Any) -> CollectionResult:
        result = await super().collect(*args, **kwargs)
        return result.model_copy(
            update={
                "exclusions": [
                    Exclusion(
                        code="query_timeout",
                        family="network",
                        message="network_errors: timeout",
                    )
                ]
            }
        )


class DownSource(SyntheticMetricsSource):
    async def capabilities(self, *args: Any, **kwargs: Any) -> list[MetricCapability]:
        raise SourceError(SourceErrorKind.UNAVAILABLE, "cannot reach source: refused")


def make_client(
    tmp_path: Path,
    source: Any = None,
    provider: Any = None,
    limits: RunnerLimits | None = None,
    **settings: Any,
) -> TestClient:
    config = {
        "_env_file": None,
        "data_dir": tmp_path,
        "ai_provider": "fake",
        **settings,
    }
    app = create_app(load_settings(**config), source=source, provider=provider, limits=limits)
    return TestClient(app)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def wait(client: TestClient, analysis_id: str, *states: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/analyses/{analysis_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not reach {states}: {job}")


def project_id(client: TestClient, name: str = SHOP_NAME, url: str = "synthetic://incident") -> str:
    """ID of the named project, created on first use (projects persist across restarts)."""
    for project in client.get("/api/projects").json()["items"]:
        if project["name"] == name:
            return str(project["project_id"])
    res = client.post(
        "/api/projects",
        json={
            "name": name,
            "matchers": SHOP_MATCHERS,
            "sources": [{"kind": "prometheus", "url": url}],
        },
    )
    assert res.status_code == 201, res.text
    return str(res.json()["project_id"])


def submit(client: TestClient, **body: Any) -> Any:
    return client.post("/api/analyses", json={"project_id": project_id(client), **body})


# --- validation ---------------------------------------------------------------------------


def test_submission_validation(client: TestClient) -> None:
    missing = client.post("/api/analyses", json={"project_id": "nope"})
    assert missing.status_code == 404 and missing.json()["code"] == "project_not_found"
    sourceless = client.post("/api/projects", json={"name": "empty", "matchers": SHOP_MATCHERS})
    res = client.post("/api/analyses", json={"project_id": sourceless.json()["project_id"]})
    assert res.status_code == 409 and res.json()["code"] == "source_not_configured"
    future = submit(client, end_time=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    assert future.status_code == 422 and future.json()["code"] == "end_time_invalid"
    naive = submit(client, end_time="2026-09-30T10:00:00")
    assert naive.status_code == 422 and naive.json()["code"] == "validation_error"
    res = client.get("/api/analyses", params={"limit": 0})
    assert res.status_code == 422


def test_health_and_config(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["database"] == "ok" and health["status"] == "ok"
    config = client.get("/api/config").json()
    assert config["report_count"] == 0


# --- lifecycle ----------------------------------------------------------------------------


def test_submit_progress_and_report(client: TestClient) -> None:
    end = datetime.now(UTC).replace(second=17, microsecond=0) - timedelta(hours=1)
    res = submit(client, end_time=end.isoformat())
    assert res.status_code == 202
    job = res.json()["analysis"]
    frozen = datetime.fromisoformat(job["end_time"])
    assert frozen.timestamp() % 300 == 0 and frozen <= end
    done = wait(client, job["analysis_id"], "completed", "partial", "failed")
    assert done["state"] == "completed", done
    assert done["report_available"] and done["explanation_status"] == "succeeded"
    assert all(s["status"] in ("done", "skipped") for s in done["stages"])
    res = client.get(f"/api/analyses/{job['analysis_id']}/report")
    assert res.headers["content-encoding"] == "gzip"  # multi-MB reports must not ship raw
    report = res.json()
    assert report["scope"] == {
        "project_id": project_id(client),
        "project_name": SHOP_NAME,
        "matchers": SHOP_MATCHERS,
    }
    assert report["windows"]["end_time"] == job["end_time"]
    assert report["source"]["backend"] == "synthetic"
    assert sum(done["finding_counts"].values()) == len(report["findings"]) > 0
    ids = {f["finding_id"] for f in report["findings"]}
    for h in report["explanation"]["explanation"]["hypotheses"]:
        assert set(h["finding_ids"]) <= ids
    assert len(done["daily_episodes"]) == 14
    listed = client.get("/api/analyses", params={"project_id": project_id(client)}).json()
    assert [j["analysis_id"] for j in listed["items"]] == [job["analysis_id"]]
    summary = client.get("/api/projects").json()["items"][0]
    assert summary["latest_analysis"] == done and summary["active_analysis"] is None
    assert client.get("/api/config").json()["report_count"] == 1


def test_duplicate_submission_returns_active_job(tmp_path: Path) -> None:
    source = GatedSource()
    with make_client(tmp_path, source=source) as client:
        first = submit(client).json()["analysis"]
        second = submit(client)
        assert second.status_code == 200 and second.json()["duplicate_of_active"]
        assert second.json()["analysis"]["analysis_id"] == first["analysis_id"]
        report = client.get(f"/api/analyses/{first['analysis_id']}/report")
        assert report.status_code == 409 and report.json()["code"] == "report_not_ready"
        source.gate.set()
        wait(client, first["analysis_id"], "completed")


def test_queue_limit(tmp_path: Path) -> None:
    source = GatedSource()
    limits = RunnerLimits(max_running=1, max_queued=1)
    with make_client(tmp_path, source=source, limits=limits) as client:
        now = datetime.now(UTC)
        ids = []
        for hours in (1, 2):
            res = submit(client, end_time=(now - timedelta(hours=hours)).isoformat())
            ids.append(res.json()["analysis"]["analysis_id"])
            assert source.started.wait(5)
        full = submit(client, end_time=(now - timedelta(hours=3)).isoformat())
        assert full.status_code == 429 and full.json()["code"] == "queue_full"
        assert full.headers["retry-after"] == "30"
        source.gate.set()
        for analysis_id in ids:
            wait(client, analysis_id, "completed")


def test_cancel_running_queued_and_finished(tmp_path: Path) -> None:
    source = GatedSource()
    with make_client(tmp_path, source=source) as client:
        now = datetime.now(UTC)
        running = submit(client, end_time=(now - timedelta(hours=1)).isoformat()).json()
        queued = submit(client, end_time=(now - timedelta(hours=2)).isoformat()).json()
        assert source.started.wait(5)
        rid, qid = running["analysis"]["analysis_id"], queued["analysis"]["analysis_id"]
        res = client.delete(f"/api/analyses/{qid}")
        assert res.status_code == 202 and res.json()["state"] == "cancelled"
        res = client.delete(f"/api/analyses/{rid}")
        assert res.status_code == 202 and res.json()["state"] == "cancelled"
        assert res.json()["error"] is None  # contract: cancelled jobs carry no error
        assert client.get(f"/api/analyses/{rid}/report").json()["code"] == "report_unavailable"
        again = client.delete(f"/api/analyses/{rid}")
        assert again.status_code == 409 and again.json()["code"] == "analysis_not_active"
        assert client.delete("/api/analyses/nope").status_code == 404
        source.gate.set()
        after = submit(client, end_time=(now - timedelta(hours=4)).isoformat()).json()
        assert wait(client, after["analysis"]["analysis_id"], "completed")["state"] == "completed"
        assert client.get(f"/api/analyses/{qid}").json()["state"] == "cancelled"


def test_ai_failure_keeps_numerical_report(tmp_path: Path) -> None:
    with make_client(tmp_path, provider=FakeExplanationProvider(fail_with="timeout")) as client:
        job = submit(client).json()["analysis"]
        done = wait(client, job["analysis_id"], "completed", "failed", "partial")
        assert done["state"] == "completed" and done["explanation_status"] == "failed"
        report = client.get(f"/api/analyses/{job['analysis_id']}/report").json()
        assert report["findings"] and report["explanation"]["reason"] == "timeout"


def test_ai_disabled(tmp_path: Path) -> None:
    with make_client(tmp_path, ai_provider="none") as client:
        job = submit(client).json()["analysis"]
        done = wait(client, job["analysis_id"], "completed")
        assert done["explanation_status"] == "disabled"


def test_source_errors_partial_and_failed(tmp_path: Path) -> None:
    with make_client(tmp_path, source=FlakySource()) as client:
        job = submit(client).json()["analysis"]
        done = wait(client, job["analysis_id"], "partial", "completed", "failed")
        assert done["state"] == "partial" and done["report_available"]
        report = client.get(f"/api/analyses/{job['analysis_id']}/report").json()
        assert report["state"] == "partial"
        network = next(c for c in report["coverage"] if c["family"] == "network")
        assert network["status"] in ("source_error", "unsupported")
    with make_client(tmp_path / "down", source=DownSource()) as client:
        job = submit(client).json()["analysis"]
        done = wait(client, job["analysis_id"], "failed")
        assert done["error"]["code"] == "metrics_source_unavailable"
        assert not done["report_available"]


def test_report_and_interrupted_job_survive_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        done_id = submit(client).json()["analysis"]["analysis_id"]
        wait(client, done_id, "completed")
        original = client.get(f"/api/analyses/{done_id}/report").json()
    source = GatedSource()
    with make_client(tmp_path, source=source) as client:
        stuck = submit(client, end_time=(datetime.now(UTC) - timedelta(hours=3)).isoformat())
        stuck_id = stuck.json()["analysis"]["analysis_id"]
        assert source.started.wait(5)
    # process "restarts" while stuck_id was running
    with make_client(tmp_path) as client:
        job = client.get(f"/api/analyses/{stuck_id}").json()
        assert job["state"] == "failed" and job["error"]["code"] == "interrupted_by_restart"
        assert client.get(f"/api/analyses/{done_id}/report").json() == original
        assert client.get("/api/config").json()["report_count"] == 1


def test_scope_isolation_and_pagination(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        now = datetime.now(UTC)
        ids = [
            submit(client, end_time=(now - timedelta(hours=h)).isoformat()).json()["analysis"][
                "analysis_id"
            ]
            for h in (1, 2, 3)
        ]
        for analysis_id in ids:
            wait(client, analysis_id, "completed")
        pid = {"project_id": project_id(client)}
        page = client.get("/api/analyses", params={**pid, "limit": 2}).json()
        assert [j["analysis_id"] for j in page["items"]] == ids[::-1][:2]
        rest = client.get(
            "/api/analyses", params={**pid, "limit": 2, "cursor": page["next_cursor"]}
        ).json()
        assert [j["analysis_id"] for j in rest["items"]] == [ids[0]] and not rest["next_cursor"]
        other_id = project_id(client, name="other", url="synthetic://healthy")
        other = client.get("/api/analyses", params={"project_id": other_id}).json()
        assert other["items"] == []
        assert len(client.get("/api/analyses").json()["items"]) == 3


def test_unsupported_saved_schema_is_reported_not_crashed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        analysis_id = submit(client).json()["analysis"]["analysis_id"]
        wait(client, analysis_id, "completed")
        with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
            db.execute("UPDATE reports SET schema_version='3.0'")
        res = client.get(f"/api/analyses/{analysis_id}/report")
        assert res.status_code == 404 and res.json()["code"] == "schema_unsupported"


async def test_pipeline_is_usable_without_the_web_framework() -> None:
    config = DetectorConfig()
    pipeline = AnalysisPipeline(
        StaticSources(SyntheticMetricsSource("degraded")),
        RobustDetector(),
        config,
        FakeExplanationProvider(),
    )
    end = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
    request = AnalysisRequest(
        scope=make_scope(),
        end_time=end,
        detector_version=config.version,
        config_hash=config.config_hash,
    )

    class Progress:
        def __init__(self) -> None:
            self.stages: list[StageProgress] = []

        async def update(self, progress: StageProgress) -> None:
            self.stages.append(progress)

    progress = Progress()
    report = await pipeline.run(
        "01999a2b-0000-7000-8000-00000000000a", request, progress, CancellationToken()
    )
    assert report.windows.end_time == end and report.findings
    assert {p.stage.value for p in progress.stages} >= {
        "discovery",
        "collection",
        "detection",
        "explanation",
    }


def test_report_size_budget_disclosed() -> None:
    config = DetectorConfig()
    pipeline = AnalysisPipeline(
        StaticSources(SyntheticMetricsSource("incident")),
        RobustDetector(),
        config,
        None,
        max_report_bytes=46_000,
    )
    end = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
    request = AnalysisRequest(
        scope=make_scope(),
        end_time=end,
        detector_version=config.version,
        config_hash=config.config_hash,
    )

    class Nop:
        async def update(self, progress: StageProgress) -> None:
            pass

    report = asyncio.run(
        pipeline.run("01999a2b-0000-7000-8000-00000000000b", request, Nop(), CancellationToken())
    )
    assert report.state.value == "partial"
    assert any(e.code == "evidence_dropped" for e in report.exclusions)
    assert all(len(f.evidence_ids) == 1 for f in report.findings)
