"""Notifications of scheduled reports (T025, T026): contracts, policy, email content,
notification channels (CRUD, secrets, usage), fan-out delivery of schedule notifications,
migration, and API. Telegram specifics are in test_telegram.py."""

import asyncio
import email
import email.policy
import json
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.common import Severity
from app.domain.jobs import AnalysisJob, ErrorCode, JobError, JobState
from app.domain.notifications import (
    AnyDestination,
    ChannelKind,
    EmailDestination,
    NotificationChannelInput,
    NotificationPolicy,
    NotifyWhen,
    ScheduleNotification,
    TelegramDestinationInput,
)
from app.domain.report import AnalysisReport, ReportSummary
from app.domain.schedule import ReportSchedule
from app.main import create_app
from app.notifications.drivers.email.render import RenderedEmail, render_email
from app.notifications.drivers.email.transport import SmtpTransport, build_message
from app.notifications.message import ReportMessage, ranked
from app.notifications.message import build_message as build_report_message
from app.notifications.policy import skip_reason
from app.settings import ConfigError, load_settings
from app.storage.db import MIGRATIONS, make_engine, schedule_notifications

REPO = Path(__file__).resolve().parents[2]
MATCHERS = [{"name": "env", "value": "production"}, {"name": "project", "value": "shop"}]
TOKEN = "123456:AAH-test-token-0123456789abcdefghij"
EMAIL_CHANNEL: dict[str, Any] = {
    "name": "On-call email",
    "destination": {"kind": "email", "recipients": ["oncall@example.com"]},
}
TELEGRAM_CHANNEL: dict[str, Any] = {
    "name": "Ops chat",
    "destination": {
        "kind": "telegram",
        "bot_token": TOKEN,
        "chat_id": "-1001000000001",
        "message_thread_id": 7,
    },
}
ALWAYS = {"when": "always"}


def fixture_report(name: str) -> AnalysisReport:
    return AnalysisReport.model_validate_json((REPO / f"fixtures/reports/{name}.json").read_text())


def fixture_job(**update: Any) -> AnalysisJob:
    job = AnalysisJob.model_validate_json((REPO / "fixtures/jobs/job_completed.json").read_text())
    return job.model_copy(update=update)


def summary_of(name: str) -> ReportSummary:
    return fixture_report(name).summary()


def message_of(job: AnalysisJob, summary: ReportSummary | None, **kw: Any) -> ReportMessage:
    return build_report_message("shop", job, summary, **kw)


def email_of(job: AnalysisJob, summary: ReportSummary | None, **kw: Any) -> RenderedEmail:
    return render_email(message_of(job, summary, **kw))


def outbox(tmp_path: Path) -> list[EmailMessage]:
    files = sorted((tmp_path / "outbox").glob("*.eml")) if (tmp_path / "outbox").exists() else []
    return [email.message_from_bytes(f.read_bytes(), policy=email.policy.default) for f in files]


def telegram_outbox(tmp_path: Path) -> list[dict[str, Any]]:
    folder = tmp_path / "outbox" / "telegram"
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    return [json.loads(f.read_text()) for f in files]


def body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain",))
    assert part is not None
    return str(part.get_content())


def policy(when: NotifyWhen = NotifyWhen.PROBLEMS, at: Severity = Severity.HIGH) -> Any:
    return NotificationPolicy(when=when, min_severity=at)


# --- contract ---------------------------------------------------------------------------------


def test_channel_input_is_normalized() -> None:
    channel = NotificationChannelInput(
        name="  On-call\n email ",
        description="  ",
        destination=EmailDestination(
            recipients=[" a@example.com", "A@Example.com", "b@example.org"]
        ),
    )
    notification = ScheduleNotification(channel_id="c1")

    assert isinstance(channel.destination, EmailDestination)
    assert channel.destination.recipients == ["a@example.com", "b@example.org"]
    assert channel.name == "On-call email"
    assert channel.description is None
    assert notification.enabled is True
    assert notification.policy.when is NotifyWhen.PROBLEMS
    assert notification.policy.min_severity is Severity.HIGH


@pytest.mark.parametrize(
    "address",
    [
        "not-an-address",
        "a@b",
        "Ops <ops@example.com>",
        "a@example.com, b@example.com",
        "a@example.com\nBcc: x@example.com",
        "a b@example.com",
    ],
)
def test_invalid_recipients_are_rejected(address: str) -> None:
    with pytest.raises(ValidationError):
        EmailDestination(recipients=[address])


def test_schedule_limits_notifications_and_uses_each_channel_once() -> None:
    notifications = [ScheduleNotification(channel_id=f"c{i}") for i in range(11)]

    with pytest.raises(ValidationError, match="at most 10"):
        ReportSchedule(time="08:00", notifications=notifications)
    with pytest.raises(ValidationError, match="each channel can be used once"):
        ReportSchedule(time="08:00", notifications=[notifications[0]] * 2)
    with pytest.raises(ValidationError, match="blank"):
        NotificationChannelInput(name="  ", destination=EmailDestination(recipients=["a@x.io"]))


@pytest.mark.parametrize("chat_id", ["-1001234567890", "123456789", "@ops_alerts", " -42 "])
def test_telegram_chat_ids_are_accepted(chat_id: str) -> None:
    destination = TelegramDestinationInput(chat_id=chat_id)

    assert destination.chat_id == chat_id.strip()


@pytest.mark.parametrize("chat_id", ["", "ops", "@ab", "https://t.me/ops", "-12a"])
def test_invalid_telegram_chat_ids_are_rejected(chat_id: str) -> None:
    with pytest.raises(ValidationError):
        TelegramDestinationInput(chat_id=chat_id)


def test_telegram_bot_tokens_are_checked_and_stripped() -> None:
    destination = TelegramDestinationInput.model_validate(
        {"chat_id": "-1", "bot_token": f"  {TOKEN}\n"}
    )

    assert destination.bot_token is not None
    assert destination.bot_token.get_secret_value() == TOKEN
    with pytest.raises(ValidationError, match="not a Telegram bot token"):
        TelegramDestinationInput.model_validate({"chat_id": "-1", "bot_token": "-1001234"})


# --- policy -----------------------------------------------------------------------------------


def test_problems_policy_sends_on_severity_failure_or_partial() -> None:
    summary = summary_of("report_anomalies")
    medium = [f for f in summary.findings if f.severity is Severity.MEDIUM]
    anomalies = summary.model_copy(update={"findings": medium})
    completed = message_of(fixture_job(), anomalies)
    partial = message_of(fixture_job(state=JobState.PARTIAL), anomalies)
    failed = message_of(fixture_job(state=JobState.FAILED), None)

    assert medium
    assert skip_reason(policy(at=Severity.MEDIUM), completed) is None
    assert skip_reason(policy(), completed) == "no finding at or above high severity"
    assert skip_reason(policy(), partial) is None
    assert skip_reason(policy(), failed) is None


def test_always_policy_sends_reports_without_findings() -> None:
    message = message_of(fixture_job(), summary_of("report_healthy"))

    assert skip_reason(policy(NotifyWhen.ALWAYS), message) is None


# --- email content ----------------------------------------------------------------------------


def test_report_with_findings_lists_them_by_severity_and_links() -> None:
    summary = summary_of("report_anomalies")
    link = "https://assistant.example.com/reports/abc"

    rendered = email_of(fixture_job(), summary, timezone="Europe/Berlin", link=link)

    top = ranked(summary.findings)[0]
    assert rendered.subject.startswith("[DevOps Assistant] shop: ")
    assert top.title in rendered.text
    assert rendered.text.index(top.title) < rendered.text.index("Open the report")
    assert f"Open the report: {link}" in rendered.text
    assert f'href="{link}"' in rendered.html
    assert "CET" in rendered.text or "CEST" in rendered.text


def test_report_without_findings_never_reads_as_healthy_when_signals_were_skipped() -> None:
    partial = email_of(
        fixture_job(state=JobState.PARTIAL), summary_of("report_partial_source_error")
    )
    healthy = email_of(fixture_job(), summary_of("report_healthy"))

    assert partial.subject == "[DevOps Assistant] shop: no anomalies found (partial)"
    assert "No anomaly there does not mean healthy" in partial.text
    assert healthy.subject == "[DevOps Assistant] shop: no anomalies found"
    assert "No anomalies found in the evaluated signals." in healthy.text
    assert "does not mean healthy" not in healthy.text


def test_failed_and_partial_analyses_say_so() -> None:
    failed = fixture_job(
        state=JobState.FAILED,
        report_available=False,
        error=JobError(code=ErrorCode.METRICS_SOURCE_UNAVAILABLE, message="connection refused"),
    )
    partial = fixture_job(state=JobState.PARTIAL)

    failed_email = email_of(failed, None)
    partial_email = email_of(partial, summary_of("report_partial_source_error"))

    assert failed_email.subject == "[DevOps Assistant] shop: analysis failed"
    assert "Analysis failed: connection refused" in failed_email.text
    assert "(partial)" in partial_email.subject
    assert "Not included:" in partial_email.text


def test_report_values_are_escaped_and_never_break_headers() -> None:
    summary = summary_of("report_anomalies")
    finding = summary.findings[0].model_copy(update={"title": "<script>alert(1)</script>"})
    hostile = summary.model_copy(update={"findings": [finding]})

    rendered = render_email(
        build_report_message("shop\r\nBcc: x@example.com", fixture_job(), hostile)
    )
    message = build_message("from@example.com", ["to@example.com"], rendered)

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "\n" not in rendered.subject
    assert message["Bcc"] is None


# --- delivery ---------------------------------------------------------------------------------


def make_client(tmp_path: Path, **config: Any) -> TestClient:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake", **config)
    return TestClient(create_app(settings))


def create_channel(client: TestClient, body: dict[str, Any]) -> str:
    res = client.post("/api/notification-channels", json=body)
    assert res.status_code == 201, res.text
    return str(res.json()["channel_id"])


def notify(channel_id: str, **policy_: Any) -> dict[str, Any]:
    return {"channel_id": channel_id, "policy": {**ALWAYS, **policy_}}


def project_body(due: datetime, notifications: list[dict[str, Any]] | None) -> Any:
    schedule: dict[str, Any] = {"time": due.strftime("%H:%M"), "timezone": "UTC"}
    if notifications is not None:
        schedule["notifications"] = notifications
    return {
        "name": "shop",
        "matchers": MATCHERS,
        "sources": [{"kind": "prometheus", "url": "synthetic://incident"}],
        "schedule": schedule,
    }


def scheduled_project(
    client: TestClient, due: datetime, notifications: list[dict[str, Any]]
) -> str:
    res = client.post("/api/projects", json=project_body(due, notifications))
    assert res.status_code == 201, res.text
    return str(res.json()["project_id"])


def run_scheduled(client: TestClient) -> dict[str, Any]:
    """Tick the scheduler for a due run and wait until the job and its delivery are done."""
    now = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2)
    services = client.app.state.services  # type: ignore[attr-defined]
    [analysis_id] = client.portal.call(services.scheduler.tick, now)  # type: ignore[union-attr]
    wait_idle(client)
    job: dict[str, Any] = client.get(f"/api/analyses/{analysis_id}").json()
    return job


def wait_idle(client: TestClient) -> None:
    """Until every submitted job has finished, including its delivery."""
    runner = client.app.state.services.runner  # type: ignore[attr-defined]
    assert client.portal is not None
    client.portal.call(runner.idle)


def drivers(client: TestClient) -> dict[ChannelKind, Any]:
    found: dict[ChannelKind, Any] = client.app.state.services.notifier.drivers  # type: ignore[attr-defined]
    return found


def due_time() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2, hours=-1)


def by_name(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["name"]: o for o in job["notifications"]}


def two_channels(client: TestClient) -> tuple[str, str]:
    return create_channel(client, EMAIL_CHANNEL), create_channel(client, TELEGRAM_CHANNEL)


def test_scheduled_report_is_delivered_to_every_channel(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        email_provider="fake",
        telegram_provider="fake",
        app_public_url="https://assistant.example.com/",
    ) as client:
        email_id, telegram_id = two_channels(client)
        scheduled_project(client, due_time(), [notify(email_id), notify(telegram_id)])

        job = run_scheduled(client)

        assert job["trigger"] == "scheduled"
        assert job["state"] in ("completed", "partial")
        outcomes = by_name(job)
        assert outcomes["On-call email"]["status"] == "sent"
        assert outcomes["On-call email"]["kind"] == "email"
        assert outcomes["On-call email"]["channel_id"] == email_id
        assert outcomes["On-call email"]["destinations"] == 1
        assert outcomes["Ops chat"]["status"] == "sent"
        assert outcomes["Ops chat"]["kind"] == "telegram"
        [message] = outbox(tmp_path)
        link = f"https://assistant.example.com/reports/{job['analysis_id']}"
        assert message["To"] == "oncall@example.com"
        assert message["Auto-Submitted"] == "auto-generated"
        assert link in body_text(message)
        [post] = telegram_outbox(tmp_path)
        assert post["chat_id"] == -1001000000001
        assert post["message_thread_id"] == 7
        assert post["parse_mode"] == "HTML"
        assert "<b>shop</b>" in post["text"]
        assert link in post["text"]
        assert TOKEN not in json.dumps(post)


def test_one_channel_serves_projects_with_different_rules(tmp_path: Path) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        scheduled_project(client, due_time(), [notify(email_id)])
        body = project_body(due_time(), [notify(email_id, when="problems", min_severity="low")])
        client.post("/api/projects", json={**body, "name": "blog"})

        channel = client.get(f"/api/notification-channels/{email_id}").json()
        projects = {p["name"]: p for p in client.get("/api/projects").json()["items"]}

        assert [u["name"] for u in channel["used_by"]] == ["blog", "shop"]
        assert projects["blog"]["schedule"]["notifications"] == [
            {
                "channel_id": email_id,
                "enabled": True,
                "policy": {"when": "problems", "min_severity": "low"},
            }
        ]


def test_manual_analyses_and_schedules_without_notifications_deliver_nothing(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        pid = scheduled_project(client, due_time(), [])

        job = run_scheduled(client)
        client.put(f"/api/projects/{pid}", json=project_body(due_time(), [notify(email_id)]))
        submitted = client.post("/api/analyses", json={"project_id": pid}).json()["analysis"]
        wait_idle(client)
        manual = client.get(f"/api/analyses/{submitted['analysis_id']}").json()

        assert job["notifications"] == []
        assert manual["trigger"] == "manual"
        assert manual["notifications"] == []
        assert outbox(tmp_path) == []


def test_disabled_notifications_are_not_delivered_or_recorded(tmp_path: Path) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        scheduled_project(client, due_time(), [{**notify(email_id), "enabled": False}])

        job = run_scheduled(client)

        assert job["notifications"] == []
        assert outbox(tmp_path) == []


def test_unconfigured_kind_is_recorded_and_others_still_deliver(tmp_path: Path) -> None:
    with make_client(tmp_path, telegram_provider="fake") as client:
        email_id, telegram_id = two_channels(client)
        scheduled_project(client, due_time(), [notify(email_id), notify(telegram_id)])

        job = run_scheduled(client)

        outcomes = by_name(job)
        assert job["state"] in ("completed", "partial")
        assert outcomes["On-call email"]["status"] == "not_configured"
        assert "SMTP_HOST" in outcomes["On-call email"]["reason"]
        assert outcomes["Ops chat"]["status"] == "sent"


def test_a_failing_channel_never_changes_the_job_or_other_channels(tmp_path: Path) -> None:
    class Broken:
        async def send(self, message: EmailMessage) -> None:
            raise smtplib.SMTPRecipientsRefused({"oncall@example.com": (550, b"no such user")})

    with make_client(tmp_path, email_provider="fake", telegram_provider="fake") as client:
        drivers(client)[ChannelKind.EMAIL].transport = Broken()
        email_id, telegram_id = two_channels(client)
        scheduled_project(client, due_time(), [notify(email_id), notify(telegram_id)])

        job = run_scheduled(client)

        outcomes = by_name(job)
        assert job["state"] in ("completed", "partial")
        assert job["report_available"] is True
        assert outcomes["On-call email"]["status"] == "failed"
        assert outcomes["On-call email"]["reason"].startswith("SMTPRecipientsRefused")
        assert outcomes["Ops chat"]["status"] == "sent"


def test_slow_channels_time_out_and_unexpected_errors_hide_their_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Hanging:
        async def send(self, message: EmailMessage) -> None:
            await asyncio.sleep(5)

    class Leaky:
        kind = ChannelKind.TELEGRAM

        def unavailable(self) -> str | None:
            return None

        async def deliver(
            self,
            destination: AnyDestination,
            secrets: dict[str, str],
            message: ReportMessage,
            *,
            silent: bool,
        ) -> int:
            raise RuntimeError(f"POST https://api.telegram.org/bot{secrets['bot_token']}/x")

        async def test(
            self, destination: AnyDestination, secrets: dict[str, str]
        ) -> str:  # pragma: no cover
            return ""

    monkeypatch.setattr("app.notifications.notifier.DELIVERY_TIMEOUT_SECONDS", 0.05)
    with make_client(tmp_path, email_provider="fake") as client:
        drivers(client)[ChannelKind.EMAIL].transport = Hanging()
        drivers(client)[ChannelKind.TELEGRAM] = Leaky()
        email_id, telegram_id = two_channels(client)
        scheduled_project(client, due_time(), [notify(email_id), notify(telegram_id)])

        job = run_scheduled(client)

        outcomes = by_name(job)
        assert outcomes["On-call email"]["status"] == "failed"
        assert outcomes["On-call email"]["reason"] == "Delivery exceeded 0 s."
        assert outcomes["Ops chat"]["status"] == "failed"
        assert outcomes["Ops chat"]["reason"] == "Unexpected error: RuntimeError"
        assert TOKEN not in json.dumps(job)


def test_policy_skip_is_recorded_per_notification(tmp_path: Path) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        strict_id = create_channel(
            client,
            {"name": "Critical only", "destination": {"kind": "email", "recipients": ["c@x.io"]}},
        )
        strict = notify(strict_id, when="problems", min_severity="critical")
        scheduled_project(client, due_time(), [notify(email_id), strict])

        job = run_scheduled(client)

        outcomes = by_name(job)
        status = outcomes["Critical only"]["status"]
        assert outcomes["On-call email"]["status"] == "sent"
        assert status in ("sent", "skipped")
        assert (status == "sent") == (
            job["state"] != "completed" or bool(job["finding_counts"]["critical"])
        )
        assert len(outbox(tmp_path)) == (2 if status == "sent" else 1)


def test_an_edited_channel_is_used_from_the_next_run(tmp_path: Path) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        scheduled_project(client, due_time(), [notify(email_id)])
        moved = {**EMAIL_CHANNEL, "destination": {"kind": "email", "recipients": ["new@x.io"]}}

        client.put(f"/api/notification-channels/{email_id}", json=moved)
        run_scheduled(client)

        [message] = outbox(tmp_path)
        assert message["To"] == "new@x.io"


def test_smtp_transport_uses_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append(f"connect {host}:{port}")

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *_: object) -> None:
            calls.append("quit")

        def starttls(self, context: object) -> None:
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            calls.append(f"login {user}:{password}")

        def send_message(self, message: EmailMessage) -> None:
            calls.append(f"send {message['To']}")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = load_settings(
        _env_file=None,
        smtp_host="mail.example.com",
        smtp_from="assistant@example.com",
        smtp_username="bot",
        smtp_password="s3cret",
    )
    message = build_message(
        "assistant@example.com", ["a@example.com"], RenderedEmail("s", "text", "<p>html</p>")
    )

    SmtpTransport(settings)._send(message)

    assert calls == [
        "connect mail.example.com:587",
        "starttls",
        "login bot:s3cret",
        "send a@example.com",
        "quit",
    ]


# --- channels and storage ---------------------------------------------------------------------


def anchor(client: TestClient, project_id: str) -> str:
    engine = client.app.state.services.projects.engine  # type: ignore[attr-defined]
    with engine.connect() as conn:
        value: str = conn.execute(
            sa.text("SELECT schedule_anchor FROM projects WHERE project_id = :p"), {"p": project_id}
        ).scalar_one()
    return value


def notification_rows(client: TestClient) -> int:
    engine = client.app.state.services.projects.engine  # type: ignore[attr-defined]
    with engine.connect() as conn:
        count: int = conn.execute(
            sa.select(sa.func.count()).select_from(schedule_notifications)
        ).scalar_one()
    return count


def test_channel_crud_keeps_the_bot_token_write_only(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post("/api/notification-channels", json=TELEGRAM_CHANNEL)
        channel_id = created.json()["channel_id"]
        kept = {**TELEGRAM_CHANNEL["destination"], "chat_id": "@ops_alerts"}
        del kept["bot_token"]

        updated = client.put(
            f"/api/notification-channels/{channel_id}",
            json={**TELEGRAM_CHANNEL, "destination": kept},
        )
        without = client.post(
            "/api/notification-channels", json={"name": "No token", "destination": kept}
        )
        taken = client.post("/api/notification-channels", json=TELEGRAM_CHANNEL)
        listed = client.get("/api/notification-channels").json()["items"]

        assert created.status_code == 201, created.text
        assert created.json()["destination"] == {
            "kind": "telegram",
            "chat_id": "-1001000000001",
            "message_thread_id": 7,
            "bot_token_set": True,
        }
        assert TOKEN not in created.text
        assert updated.status_code == 200, updated.text
        assert updated.json()["destination"]["chat_id"] == "@ops_alerts"
        assert updated.json()["destination"]["bot_token_set"] is True
        assert without.status_code == 422
        assert without.json()["errors"][0]["field"] == "body.destination.bot_token"
        assert taken.status_code == 409
        assert taken.json()["code"] == "channel_name_taken"
        assert [c["name"] for c in listed] == ["Ops chat"]
        assert TOKEN not in json.dumps(listed)


def test_schedule_notifications_round_trip_and_never_re_anchor(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        email_id, telegram_id = two_channels(client)
        due = due_time()
        pid = scheduled_project(client, due, [notify(email_id)])
        before = anchor(client, pid)

        res = client.put(
            f"/api/projects/{pid}",
            json=project_body(due, [notify(telegram_id, when="problems"), notify(email_id)]),
        )

        assert res.status_code == 200, res.text
        assert [n["channel_id"] for n in res.json()["schedule"]["notifications"]] == [
            telegram_id,
            email_id,
        ]
        assert res.json()["schedule"]["notifications"][0]["policy"]["when"] == "problems"
        assert anchor(client, pid) == before

        res = client.put(f"/api/projects/{pid}", json={**project_body(due, None), "schedule": None})

        assert res.json()["schedule"] is None
        assert notification_rows(client) == 0


def test_unknown_channels_are_rejected(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        res = client.post("/api/projects", json=project_body(due_time(), [notify("nope")]))

        assert res.status_code == 422
        assert res.json()["errors"][0]["field"] == "body.schedule.notifications.0.channel_id"


def test_channels_in_use_cannot_be_deleted(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        email_id = create_channel(client, EMAIL_CHANNEL)
        unused_id = create_channel(client, {**EMAIL_CHANNEL, "name": "Unused"})
        pid = scheduled_project(client, due_time(), [notify(email_id)])

        used = client.delete(f"/api/notification-channels/{email_id}")
        unused = client.delete(f"/api/notification-channels/{unused_id}")
        channel = client.get(f"/api/notification-channels/{email_id}").json()

        assert used.status_code == 409
        assert used.json()["code"] == "channel_in_use"
        assert "shop" in used.json()["detail"]
        assert unused.status_code == 204
        assert channel["used_by"] == [{"project_id": pid, "name": "shop"}]


# --- migration --------------------------------------------------------------------------------


def test_migration_turns_t025_email_settings_into_channels(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "assistant.sqlite3")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0008")
    now, anchor_at = "2026-10-01T00:00:00Z", "2099-01-05T08:00:00Z"
    shared = {"recipients": ["oncall@example.com", "b@x.io"], "when": "always"}
    projects = {
        "p1": {**shared, "min_severity": "medium"},
        "p2": {"recipients": ["B@x.io", "oncall@example.com"]},
        "p3": {"recipients": ["solo@x.io"], "when": "problems", "min_severity": "critical"},
    }
    job = fixture_job().model_dump(mode="json", exclude={"notifications"})
    sent = {"status": "sent", "recipients": 1, "reason": None, "at": now}
    with engine.begin() as conn:
        for name, notify_ in projects.items():
            schedule = {"time": "08:00", "timezone": "UTC", "weekdays": ["mon"], "notify": notify_}
            conn.exec_driver_sql(
                "INSERT INTO projects (project_id, name, matchers, created_at, updated_at,"
                " schedule, schedule_anchor) VALUES (?, ?, '[]', ?, ?, ?, ?)",
                (name, name, now, now, json.dumps(schedule), anchor_at),
            )
        for analysis_id, outcome in (("a1", sent), ("a2", None)):
            conn.exec_driver_sql(
                "INSERT INTO analysis_jobs (analysis_id, project_id, end_time, config_hash, state,"
                " created_at, job) VALUES (?, 'p1', ?, 'abc', 'completed', ?, ?)",
                (
                    analysis_id,
                    now,
                    now,
                    json.dumps(
                        {
                            **job,
                            "scope": {**job["scope"], "project_id": "p1"},
                            "analysis_id": analysis_id,
                            "notification": outcome,
                        }
                    ),
                ),
            )
    engine.dispose()

    with make_client(tmp_path) as client:
        found = {p: client.get(f"/api/projects/{p}").json() for p in projects}
        channels = {c["name"]: c for c in client.get("/api/notification-channels").json()["items"]}
        emailed = client.get("/api/analyses/a1").json()
        plain = client.get("/api/analyses/a2").json()
        migrated_anchor = anchor(client, "p1")

    assert sorted(channels) == ["Email oncall@example.com +1", "Email solo@x.io"]
    shared_channel = channels["Email oncall@example.com +1"]
    assert shared_channel["destination"] == {
        "kind": "email",
        "recipients": ["oncall@example.com", "b@x.io"],
    }
    assert [u["project_id"] for u in shared_channel["used_by"]] == ["p1", "p2"]
    assert found["p1"]["schedule"]["notifications"] == [
        {
            "channel_id": shared_channel["channel_id"],
            "enabled": True,
            "policy": {"when": "always", "min_severity": "medium"},
        }
    ]
    assert found["p2"]["schedule"]["notifications"][0]["policy"] == {
        "when": "problems",
        "min_severity": "high",
    }
    assert found["p1"]["schedule"]["weekdays"] == ["mon"]
    assert migrated_anchor == anchor_at
    assert emailed["notifications"] == [
        {
            "channel_id": None,
            "name": "Email",
            "kind": "email",
            "status": "sent",
            "destinations": 1,
            "reason": None,
            "at": now,
        }
    ]
    assert plain["notifications"] == []


# --- API and settings -------------------------------------------------------------------------


def test_kinds_endpoint_reports_availability(tmp_path: Path) -> None:
    with make_client(tmp_path / "off", telegram_provider="none") as client:
        off = {c["kind"]: c for c in client.get("/api/notification-channels/kinds").json()["items"]}
    with make_client(tmp_path, email_provider="fake") as client:
        on = {c["kind"]: c for c in client.get("/api/notification-channels/kinds").json()["items"]}

    assert off["email"]["available"] is False
    assert "SMTP_HOST" in off["email"]["reason"]
    assert off["telegram"] == {
        "kind": "telegram",
        "available": False,
        "reason": "Telegram delivery is turned off (TELEGRAM_PROVIDER=none).",
    }
    assert on["email"] == {"kind": "email", "available": True, "reason": None}
    assert on["telegram"]["available"] is True


def test_test_message_endpoint(tmp_path: Path) -> None:
    destination = {"kind": "email", "recipients": ["a@example.com", "A@example.com"]}
    with make_client(tmp_path / "off") as client:
        res = client.post("/api/notification-channels/test", json={"destination": destination})

        assert res.status_code == 409
        assert res.json()["code"] == "channel_not_configured"

    with make_client(tmp_path, email_provider="fake") as client:
        res = client.post("/api/notification-channels/test", json={"destination": destination})
        bad = client.post(
            "/api/notification-channels/test",
            json={"destination": {"kind": "email", "recipients": ["nope"]}},
        )

        assert res.status_code == 200, res.text
        assert res.json()["kind"] == "email"
        assert res.json()["detail"] == "sent to a@example.com"
        assert [m["Subject"] for m in outbox(tmp_path)] == ["[DevOps Assistant] Test email"]
        assert bad.status_code == 422


def test_notification_settings_are_validated() -> None:
    configured = load_settings(_env_file=None, smtp_host="mail", smtp_from="a@example.com")

    assert configured.email_configured
    assert not load_settings(_env_file=None, smtp_host="mail").email_configured
    assert not load_settings(_env_file=None, email_provider="none").email_configured
    with pytest.raises(ConfigError, match="SMTP_USERNAME and SMTP_PASSWORD"):
        load_settings(_env_file=None, smtp_host="mail", smtp_username="bot")
    with pytest.raises(ConfigError, match="APP_PUBLIC_URL"):
        load_settings(_env_file=None, app_public_url="assistant.example.com")
    with pytest.raises(ConfigError, match="SMTP_FROM"):
        load_settings(_env_file=None, smtp_from="Ops <ops@example.com>")
    with pytest.raises(ConfigError, match="TELEGRAM_API_URL"):
        load_settings(_env_file=None, telegram_api_url="api.telegram.org")
    assert (
        load_settings(_env_file=None, app_public_url="https://x.example.com/").app_public_url
        == "https://x.example.com"
    )
    assert (
        load_settings(_env_file=None, telegram_api_url="http://bot-api:8081/").telegram_api_url
        == "http://bot-api:8081"
    )
