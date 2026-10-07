"""Project-scoped analyses, cascade delete, demo seeding, and the pre-project upgrade (T012)."""

import json
import logging
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import load_settings
from app.sources.prometheus.synthetic import SCENARIOS
from app.storage.db import MIGRATIONS, make_engine

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MATCHERS = [{"name": "env", "value": "production"}, {"name": "project", "value": "shop"}]


def make_client(data_dir: Path, **settings: Any) -> TestClient:
    config = {"_env_file": None, "data_dir": data_dir, "ai_provider": "fake", **settings}

    return TestClient(create_app(load_settings(**config)))


def create_project(client: TestClient, name: str, url: str) -> str:
    res = client.post(
        "/api/projects",
        json={
            "name": name,
            "matchers": MATCHERS,
            "sources": [{"kind": "prometheus", "url": url}],
        },
    )
    assert res.status_code == 201, res.text

    return str(res.json()["project_id"])


def run(client: TestClient, project_id: str) -> dict[str, Any]:
    res = client.post("/api/analyses", json={"project_id": project_id})
    assert res.status_code == 202, res.text
    analysis_id = res.json()["analysis"]["analysis_id"]

    for _ in range(600):
        job: dict[str, Any] = client.get(f"/api/analyses/{analysis_id}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"analysis {analysis_id} did not finish")


def test_projects_on_different_sources_stay_isolated(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        incident = create_project(client, "incident", "synthetic://incident")
        healthy = create_project(client, "healthy", "synthetic://healthy")

        a, b = run(client, incident), run(client, healthy)

        assert a["scope"]["project_id"] == incident and b["scope"]["project_id"] == healthy

        reports = {
            pid: client.get(f"/api/analyses/{job['analysis_id']}/report").json()
            for pid, job in ((incident, a), (healthy, b))
        }

        assert len(reports[incident]["findings"]) > len(reports[healthy]["findings"])
        assert reports[incident]["scope"]["project_name"] == "incident"

        for pid, job in ((incident, a), (healthy, b)):
            listed = client.get("/api/analyses", params={"project_id": pid}).json()["items"]
            assert [j["analysis_id"] for j in listed] == [job["analysis_id"]]

        summaries = {p["project_id"]: p for p in client.get("/api/projects").json()["items"]}
        assert summaries[incident]["latest_analysis"]["analysis_id"] == a["analysis_id"]
        assert summaries[healthy]["latest_analysis"]["analysis_id"] == b["analysis_id"]


def test_hard_delete_cascades_and_is_refused_while_active(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        doomed = create_project(client, "doomed", "synthetic://incident")
        kept = create_project(client, "kept", "synthetic://healthy")
        done = run(client, doomed)
        kept_job = run(client, kept)

        db = tmp_path / "assistant.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE analysis_jobs SET state='running' WHERE analysis_id=?",
                (done["analysis_id"],),
            )

        assert client.get(f"/api/projects/{doomed}").json()["report_count"] == 1

        res = client.delete(f"/api/projects/{doomed}")
        assert res.status_code == 409 and res.json()["code"] == "project_busy"

        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE analysis_jobs SET state='completed' WHERE analysis_id=?",
                (done["analysis_id"],),
            )

        assert client.delete(f"/api/projects/{doomed}").status_code == 204
        assert client.get(f"/api/analyses/{done['analysis_id']}").status_code == 404
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM project_sources").fetchone()[0] == 1

        report = client.get(f"/api/analyses/{kept_job['analysis_id']}/report")
        assert report.status_code == 200


def test_keep_reports_deletes_older_analyses(tmp_path: Path) -> None:
    def listed(client: TestClient, pid: str) -> list[str]:
        items = client.get("/api/analyses", params={"project_id": pid}).json()["items"]

        return [j["analysis_id"] for j in items]

    with make_client(tmp_path) as client:
        pid = create_project(client, "retained", "synthetic://healthy")
        other = create_project(client, "other", "synthetic://healthy")
        body = client.get(f"/api/projects/{pid}").json()
        body = {k: body[k] for k in ("name", "matchers", "sources")}

        res = client.put(f"/api/projects/{pid}", json={**body, "keep_reports": 2})
        assert res.status_code == 200 and res.json()["keep_reports"] == 2

        other_job = run(client, other)
        jobs = [run(client, pid)["analysis_id"] for _ in range(3)]

        assert listed(client, pid) == jobs[:0:-1]
        assert client.get(f"/api/analyses/{jobs[0]}/report").status_code == 404
        assert client.get(f"/api/projects/{pid}").json()["report_count"] == 2

        # Lowering the limit applies on save; other projects are untouched.
        client.put(f"/api/projects/{pid}", json={**body, "keep_reports": 1})

        assert listed(client, pid) == [jobs[2]]
        assert listed(client, other) == [other_job["analysis_id"]]

        res = client.put(f"/api/projects/{pid}", json={**body, "keep_reports": 0})
        assert res.status_code == 422


def test_demo_projects_are_seeded_once(tmp_path: Path) -> None:
    with make_client(tmp_path, demo_projects=True) as client:
        names = [p["name"] for p in client.get("/api/projects").json()["items"]]

        assert names == sorted(f"Demo: {s}" for s in SCENARIOS)

        first = client.get("/api/projects").json()["items"][0]["project_id"]
        assert client.delete(f"/api/projects/{first}").status_code == 204

    with make_client(tmp_path, demo_projects=True) as client:  # not re-seeded while any exist
        assert len(client.get("/api/projects").json()["items"]) == len(SCENARIOS) - 1


# --- upgrade from the pre-project schema --------------------------------------------------------


def legacy_database(data_dir: Path) -> tuple[str, dict[str, Any]]:
    """A database at revision 0002 holding one pre-project job and report (schema 1.0)."""
    data_dir.mkdir(parents=True)
    engine = make_engine(data_dir / "assistant.sqlite3")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))

    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0002")

    report = json.loads((FIXTURES / "reports" / "report_anomalies.json").read_text())
    report["scope"] = {"project": "shop", "env": "production"}
    report["schema_version"] = "1.0"

    job = json.loads((FIXTURES / "jobs" / "job_completed.json").read_text())
    job["scope"] = {"project": "shop", "env": "production"}
    del job["daily_episodes"]

    raw = json.dumps(report).encode()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO analysis_jobs (analysis_id, project, env, end_time, config_hash, state,"
            " created_at, job) VALUES (?, 'shop', 'production', ?, ?, 'completed', ?, ?)",
            (
                job["analysis_id"],
                job["end_time"],
                job["config_hash"],
                job["created_at"],
                json.dumps(job),
            ),
        )
        conn.exec_driver_sql(
            "INSERT INTO reports (analysis_id, schema_version, created_at, size_bytes, body)"
            " VALUES (?, '1.0', ?, ?, ?)",
            (job["analysis_id"], report["generated_at"], len(raw), zlib.compress(raw)),
        )

    engine.dispose()
    return job["analysis_id"], report


def test_upgrade_creates_projects_and_keeps_reports(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    analysis_id, legacy = legacy_database(tmp_path / "data")
    legacy_settings = {
        "metrics_url": "https://vm.example/prometheus",
        "metrics_bearer_token": "legacy-token",
    }
    caplog.set_level(logging.WARNING, logger="app")

    with make_client(tmp_path / "data", **legacy_settings) as client:
        [project] = client.get("/api/projects").json()["items"]

        assert project["name"] == "shop / production"
        assert project["matchers"] == MATCHERS
        assert project["sources"] == [
            {
                "kind": "prometheus",
                "url": "https://vm.example/prometheus",
                "tls_verify": True,
                "auth": {"type": "bearer", "token_set": True},
            }
        ]
        assert project["latest_analysis"]["analysis_id"] == analysis_id
        assert "legacy-token" not in client.get("/api/projects").text

        report = client.get(f"/api/analyses/{analysis_id}/report").json()

        assert report["schema_version"] == "2.0"
        assert report["scope"] == {
            "project_id": project["project_id"],
            "project_name": "shop / production",
            "matchers": MATCHERS,
        }
        assert {k: v for k, v in report.items() if k not in ("scope", "schema_version")} == {
            k: v for k, v in legacy.items() if k not in ("scope", "schema_version")
        }

        job = client.get(f"/api/analyses/{analysis_id}").json()

        assert job["scope"] == report["scope"]

        assert "attached METRICS_URL to 1 migrated project" in caplog.text

        # The source can be removed deliberately; the one-time import never re-adds it.
        res = client.put(
            f"/api/projects/{project['project_id']}",
            json={"name": project["name"], "matchers": MATCHERS, "sources": []},
        )
        assert res.status_code == 200

    with make_client(tmp_path / "data", **legacy_settings) as client:
        [project] = client.get("/api/projects").json()["items"]

        assert project["sources"] == []

        with sqlite3.connect(tmp_path / "data" / "assistant.sqlite3") as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("PRAGMA foreign_keys").fetchone() == (0,)  # per-connection default


def test_upgrade_without_legacy_source_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    legacy_database(tmp_path / "data")
    caplog.set_level(logging.WARNING, logger="app")

    with make_client(tmp_path / "data") as client:
        [project] = client.get("/api/projects").json()["items"]

        assert project["sources"] == []

        res = client.post("/api/analyses", json={"project_id": project["project_id"]})
        assert res.status_code == 409 and res.json()["code"] == "source_not_configured"

    assert "have no metrics source" in caplog.text
