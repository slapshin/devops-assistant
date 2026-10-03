"""Packaging-level behaviour: UI serving, detector overrides, maintenance commands."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app, load_detector_config
from app.maintenance import main as maintenance
from app.settings import ConfigError, load_settings


def settings(tmp_path: Path, **kw: Any) -> Any:
    return load_settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ai_provider="none",
        **kw,
    )


def test_ui_is_served_with_spa_fallback_and_api_untouched(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><div id=root></div>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    with TestClient(create_app(settings(tmp_path, ui_static_dir=ui))) as client:
        assert "id=root" in client.get("/").text
        assert "id=root" in client.get("/reports/abc/findings").text  # deep link
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert "nope" not in client.get("/../secret.txt").text
        assert client.get("/api/nope").status_code == 404
        assert client.get("/api/health").json()["status"] == "ok"


def test_detector_config_override_changes_hash(tmp_path: Path) -> None:
    override = tmp_path / "detector.json"
    override.write_text(
        json.dumps(
            {
                "z_threshold": 5.0,
                "signals": {
                    "cpu_utilization": {
                        "min_abs_effect": 0.2,
                        "abs_floor": 0.01,
                        "absolute_high": 0.95,
                    }
                },
            }
        )
    )
    config = load_detector_config(settings(tmp_path, detector_config_file=override))
    default = load_detector_config(settings(tmp_path))
    assert config.z_threshold == 5.0 and config.signals["cpu_utilization"].absolute_high == 0.95
    assert "memory_utilization" in config.signals
    assert config.config_hash != default.config_hash
    bad = tmp_path / "bad.json"
    bad.write_text('{"z_threshold": "high"}')
    with pytest.raises(ConfigError, match="DETECTOR_CONFIG_FILE"):
        load_detector_config(settings(tmp_path, detector_config_file=bad))


def test_backup_and_prune_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = settings(tmp_path)
    with TestClient(create_app(s)) as client:
        project = client.post(
            "/api/projects",
            json={
                "name": "paas",
                "matchers": [{"name": "project", "value": "paas"}],
                "sources": [{"kind": "prometheus", "url": "synthetic://healthy"}],
            },
        ).json()
        analysis_id = client.post(
            "/api/analyses", json={"project_id": project["project_id"]}
        ).json()["analysis"]["analysis_id"]
        for _ in range(200):
            if client.get(f"/api/analyses/{analysis_id}").json()["state"] == "completed":
                break
            time.sleep(0.05)
    monkeypatch.setenv("DATA_DIR", str(s.data_dir))
    copy = tmp_path / "backup" / "copy.sqlite3"
    assert maintenance(["backup", str(copy)]) == 0
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert maintenance(["backup", str(copy)]) == 1  # never overwrites
    with sqlite3.connect(s.database_path) as db:
        db.execute("UPDATE analysis_jobs SET created_at='2020-01-01T00:00:00Z'")
    assert maintenance(["prune", "--older-than", "30"]) == 0  # dry run
    assert "would delete" in capsys.readouterr().out
    with sqlite3.connect(s.database_path) as db:
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert maintenance(["prune", "--older-than", "30", "--yes"]) == 0
    with sqlite3.connect(s.database_path) as db:
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 0
