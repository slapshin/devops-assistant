"""Per-project report schedules: occurrence maths, API round trip, and the scheduler."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.schedule import ReportSchedule, Weekday
from app.main import create_app
from app.scheduler import MISSED_RUN_GRACE
from app.settings import load_settings

MATCHERS = [{"name": "env", "value": "production"}, {"name": "project", "value": "shop"}]


def utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# --- occurrences ------------------------------------------------------------------------------


def test_daily_schedule_follows_local_time_across_dst() -> None:
    berlin = ReportSchedule(time="08:00", timezone="Europe/Berlin")

    # Summer (CEST, UTC+2) and winter (CET, UTC+1).
    assert berlin.next_after(utc(2026, 7, 1, 5, 0)) == utc(2026, 7, 1, 6, 0)
    assert berlin.next_after(utc(2026, 12, 1, 5, 0)) == utc(2026, 12, 1, 7, 0)

    # The DST switch on 2026-10-25: Saturday is UTC+2, Sunday UTC+1.
    assert berlin.next_after(utc(2026, 10, 24, 6, 0)) == utc(2026, 10, 25, 7, 0)
    assert berlin.latest_at_or_before(utc(2026, 10, 25, 6, 59)) == utc(2026, 10, 24, 6, 0)


def test_occurrence_boundaries_are_exact() -> None:
    daily = ReportSchedule(time="08:00")
    at = utc(2026, 10, 5, 8, 0)

    assert daily.latest_at_or_before(at) == at
    assert daily.next_after(at) == at + timedelta(days=1)


def test_weekdays_restrict_occurrences() -> None:
    # 2026-10-05 is a Monday.
    weekly = ReportSchedule(time="06:30", weekdays=[Weekday.FRI, Weekday.MON, Weekday.MON])

    assert weekly.weekdays == [Weekday.MON, Weekday.FRI]
    assert weekly.next_after(utc(2026, 10, 5, 7, 0)) == utc(2026, 10, 9, 6, 30)
    assert weekly.latest_at_or_before(utc(2026, 10, 8, 12, 0)) == utc(2026, 10, 5, 6, 30)

    single = ReportSchedule(time="00:00", weekdays=[Weekday.SUN])

    assert single.next_after(utc(2026, 10, 11, 0, 0)) == utc(2026, 10, 18, 0, 0)


@pytest.mark.parametrize(
    "fields",
    [
        {"time": "8:00"},
        {"time": "24:00"},
        {"time": "08:00", "timezone": "Mars/Olympus"},
        {"time": "08:00", "timezone": "../../etc/passwd"},
        {"time": "08:00", "weekdays": []},
    ],
)
def test_invalid_schedules_are_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReportSchedule.model_validate(fields)


# --- API and scheduler ------------------------------------------------------------------------


def make_client(tmp_path: Path) -> TestClient:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    return TestClient(create_app(settings))


def project_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "scheduled",
        "matchers": MATCHERS,
        "sources": [{"kind": "prometheus", "url": "synthetic://healthy"}],
        **overrides,
    }


def tick(client: TestClient, now: datetime) -> list[str]:
    scheduler = client.app.state.services.scheduler  # type: ignore[attr-defined]
    submitted: list[str] = client.portal.call(scheduler.tick, now)  # type: ignore[union-attr]
    return submitted


def daily_at(moment: datetime) -> dict[str, Any]:
    """A daily UTC schedule whose time of day is that of ``moment``."""
    return {"time": moment.strftime("%H:%M"), "timezone": "UTC"}


def test_schedule_round_trip_and_validation(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        res = client.post(
            "/api/projects",
            json=project_body(schedule={"time": "08:00", "timezone": "Europe/Berlin"}),
        )

        assert res.status_code == 201, res.text
        project = res.json()
        assert project["schedule"] == {
            "time": "08:00",
            "timezone": "Europe/Berlin",
            "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        }
        assert project["next_scheduled_run"].endswith(":00:00Z")

        res = client.put(f"/api/projects/{project['project_id']}", json=project_body())

        assert res.json()["schedule"] is None and res.json()["next_scheduled_run"] is None

        res = client.post(
            "/api/projects",
            json=project_body(name="bad", schedule={"time": "08:00", "timezone": "Nowhere/City"}),
        )

        assert res.status_code == 422
        assert res.json()["errors"][0]["field"] == "body.schedule.timezone"


def test_scheduler_submits_due_runs_once(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2)
    due = now - timedelta(hours=1)
    with make_client(tmp_path) as client:
        pid = client.post("/api/projects", json=project_body(schedule=daily_at(due))).json()[
            "project_id"
        ]

        [analysis_id] = tick(client, now)
        job = client.get(f"/api/analyses/{analysis_id}").json()

        assert job["trigger"] == "scheduled"
        assert job["scope"]["project_id"] == pid
        # The window ends at the scheduled instant (floored to the step), not at the tick.
        end = datetime.fromisoformat(job["end_time"])
        assert due - timedelta(minutes=5) < end <= due

        assert tick(client, now + timedelta(minutes=1)) == []
        assert len(tick(client, now + timedelta(days=1))) == 1


def test_scheduler_skips_missed_unsourced_and_presaved_runs(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2)
    with make_client(tmp_path) as client:
        missed = now - MISSED_RUN_GRACE - timedelta(minutes=5)
        client.post("/api/projects", json=project_body(name="missed", schedule=daily_at(missed)))
        client.post(
            "/api/projects",
            json=project_body(name="no source", sources=[], schedule=daily_at(now)),
        )

        assert tick(client, now) == []

        # Handled: neither fires again before its next occurrence.
        assert tick(client, now + timedelta(minutes=1)) == []

        # A schedule saved after today's time first fires tomorrow.
        past = datetime.now(UTC) - timedelta(minutes=10)
        client.post("/api/projects", json=project_body(name="late", schedule=daily_at(past)))

        assert tick(client, datetime.now(UTC)) == []
        assert len(tick(client, past + timedelta(days=1, minutes=1))) == 1


def test_manual_analyses_are_marked_manual(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        pid = client.post("/api/projects", json=project_body()).json()["project_id"]

        res = client.post("/api/analyses", json={"project_id": pid})

        assert res.json()["analysis"]["trigger"] == "manual"
