import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import load_settings


@pytest.fixture
def client() -> TestClient:
    settings = load_settings(
        _env_file=None, openai_api_key="sk-should-not-leak", openai_model="some-model"
    )
    return TestClient(create_app(settings))


def test_health_does_not_require_metrics_source(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["metrics_source"]["reachable"] is None


def test_config_exposes_no_secrets(client: TestClient) -> None:
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert body["metrics_source"] == "http://localhost:8428"
    assert body["explanation_status"] == "pending"
    assert len(body["config_hash"]) == 12
    assert "sk-should-not-leak" not in res.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/projects"),
        ("get", "/api/projects/paas/envs"),
        ("get", "/api/analyses"),
        ("get", "/api/analyses/x"),
        ("get", "/api/analyses/x/report"),
        ("delete", "/api/analyses/x"),
    ],
)
def test_unimplemented_routes_are_visibly_incomplete(
    client: TestClient, method: str, path: str
) -> None:
    res = client.request(method, path)
    assert res.status_code == 501
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["code"] == "not_implemented"


def test_validation_errors_are_problem_details(client: TestClient) -> None:
    res = client.post("/api/analyses", json={"project": "paas"})
    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    body = res.json()
    assert body["code"] == "validation_error"
    assert any(e["field"] == "body.env" for e in body["errors"])


def test_openapi_lists_frozen_routes(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]
    assert {
        "/api/projects",
        "/api/projects/{project}/envs",
        "/api/analyses",
        "/api/analyses/{analysis_id}",
        "/api/analyses/{analysis_id}/report",
    } <= set(paths)
    assert "delete" in paths["/api/analyses/{analysis_id}"]
