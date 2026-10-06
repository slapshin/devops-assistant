import ast
import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from app.domain.jobs import (
    AnalysisJob,
    AnalysisSubmitted,
    Problem,
    RuntimeConfig,
)
from app.domain.metrics import MetricSeries
from app.domain.projects import ConnectionTest, ProjectList
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows
from scripts.export_schemas import SCHEMAS
from scripts.generate_fixtures import build, render
from tests.helpers import make_scope

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
CONTRACTS = REPO / "docs" / "contracts"
DOMAIN = REPO / "backend" / "app" / "domain"


def fixture_model(rel: str) -> type[BaseModel] | None:
    name = Path(rel).name
    if rel.startswith("reports/"):
        return AnalysisReport
    if name.startswith("submitted_"):
        return AnalysisSubmitted
    if rel.startswith("jobs/"):
        return AnalysisJob
    if name.startswith("problem_"):
        return Problem
    discovery: dict[str, type[BaseModel]] = {
        "projects.json": ProjectList,
        "connection_test.json": ConnectionTest,
        "connection_test_cloudflare.json": ConnectionTest,
        "config.json": RuntimeConfig,
    }
    return discovery.get(name)


FIXTURE_FILES = sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json"))


def test_fixtures_are_current() -> None:
    generated = build()
    assert set(generated) == set(FIXTURE_FILES), "run: uv run python -m scripts.generate_fixtures"
    stale = [rel for rel, m in generated.items() if (FIXTURES / rel).read_text() != render(m)]
    assert not stale, f"stale fixtures {stale}; run: uv run python -m scripts.generate_fixtures"


@pytest.mark.parametrize("rel", FIXTURE_FILES)
def test_fixture_round_trips_through_model(rel: str) -> None:
    model = fixture_model(rel)
    if model is None:
        pytest.skip("manifest fixture validated in test_capability_manifest")
    data = json.loads((FIXTURES / rel).read_text())
    parsed = model.model_validate_json((FIXTURES / rel).read_text())
    assert parsed.model_dump(mode="json") == data


@pytest.mark.parametrize("rel", [r for r in FIXTURE_FILES if fixture_model(r) is not None])
def test_fixture_validates_against_exported_json_schema(rel: str) -> None:
    model = fixture_model(rel)
    assert model is not None
    schema_name = (
        next(n for n, m in SCHEMAS.items() if m is model) if model in SCHEMAS.values() else None
    )
    if schema_name is None:
        pytest.skip(f"{model.__name__} is covered by openapi.json")
    schema = json.loads((CONTRACTS / f"{schema_name}.schema.json").read_text())
    jsonschema.validate(json.loads((FIXTURES / rel).read_text()), schema)


def test_observed_manifest_is_verified_and_honest_about_gaps() -> None:
    items = json.loads(
        (FIXTURES / "metrics/capabilities_paas_production_observed.json").read_text()
    )["items"]
    assert all(c["verified"] for c in items)
    unsupported = {c["signal"] for c in items if c["status"] == "unsupported"}
    assert unsupported == {"container_memory_limit_ratio", "container_throttling_ratio"}
    assert all(not c["observed_metrics"] for c in items if c["status"] == "unsupported")


def test_capability_manifest_is_explicitly_unverified() -> None:
    items = json.loads((FIXTURES / "metrics/capabilities_supplied_unverified.json").read_text())
    assert items["items"]
    assert all(not c["verified"] for c in items["items"])


def test_fixtures_use_supplied_labels_and_no_secrets() -> None:
    text = "".join((FIXTURES / r).read_text() for r in FIXTURE_FILES)
    for expected in (
        '"project": "paas"',
        '"env": "production"',
        "paas-production",
        "dispatcher-api",
        "http_response_status_code",
        "/api/v3/tasks/:task",
    ):
        assert expected in text
    for forbidden in ("sk-", "Bearer ", "password", "OPENAI_API_KEY="):
        assert forbidden not in text


def test_domain_imports_no_framework_or_llm_sdk() -> None:
    banned = {"fastapi", "starlette", "openai", "httpx", "sqlalchemy", "uvicorn"}
    for path in DOMAIN.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"


def _report() -> AnalysisReport:
    return AnalysisReport.model_validate_json(
        (FIXTURES / "reports/report_anomalies.json").read_text()
    )


def test_report_rejects_dangling_evidence_reference() -> None:
    data = _report().model_dump(mode="json")
    data["evidence"] = data["evidence"][:1]
    with pytest.raises(ValidationError, match="unknown evidence"):
        AnalysisReport.model_validate(data)


def test_report_rejects_explanation_referencing_unknown_finding() -> None:
    data = _report().model_dump(mode="json")
    data["explanation"]["explanation"]["hypotheses"][0]["finding_ids"] = ["fnd_0000000000000000"]
    with pytest.raises(ValidationError, match="explanation references unknown findings"):
        AnalysisReport.model_validate(data)


def test_report_requires_fourteen_ordered_trend_buckets() -> None:
    data = _report().model_dump(mode="json")
    data["trends"] = data["trends"][:13]
    with pytest.raises(ValidationError):
        AnalysisReport.model_validate(data)


def test_series_coverage_must_match_gaps() -> None:
    series = _report().evidence[1].series.model_dump(mode="json")
    series["coverage"] = 1.0
    with pytest.raises(ValidationError, match="coverage"):
        MetricSeries.model_validate(series)


def test_naive_datetimes_are_rejected_and_output_is_utc_z() -> None:
    scope = make_scope().model_dump()
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(
            {
                "scope": scope,
                "end_time": "2026-09-30T10:05:00",
                "detector_version": "x",
                "config_hash": "0" * 12,
            }
        )
    req = AnalysisRequest.model_validate(
        {
            "scope": scope,
            "end_time": "2026-09-30T13:05:00+03:00",
            "detector_version": "x",
            "config_hash": "0" * 12,
        }
    )
    assert req.model_dump(mode="json")["end_time"] == "2026-09-30T10:05:00Z"


def test_request_end_time_must_be_step_aligned() -> None:
    with pytest.raises(ValidationError, match="aligned"):
        AnalysisRequest(
            scope=make_scope(),
            end_time=datetime(2026, 9, 30, 10, 7, tzinfo=UTC),
            detector_version="x",
            config_hash="0" * 12,
        )


def test_windows_are_half_open_and_consecutive() -> None:
    t = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
    w = AnalysisWindows.for_end(t)
    assert w.latest_day.end == t and w.latest_day.start == t - timedelta(days=1)
    assert w.trend.start == t - timedelta(days=14)
    trends = _report().trends
    for newer, older in pairwise(trends):
        assert older.window.end == newer.window.start
