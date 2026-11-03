"""Email notifications of scheduled reports (T025): policy, content, delivery, and API."""

import email
import email.policy
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.common import Severity
from app.domain.jobs import AnalysisJob, ErrorCode, JobError, JobState
from app.domain.notifications import EmailNotification, NotifyWhen
from app.domain.report import AnalysisReport, ReportSummary
from app.main import create_app
from app.notifications.notifier import skip_reason
from app.notifications.render import RenderedEmail, ranked, render_email
from app.notifications.transport import SmtpTransport, build_message
from app.settings import ConfigError, load_settings

REPO = Path(__file__).resolve().parents[2]
MATCHERS = [{"name": "env", "value": "production"}, {"name": "project", "value": "shop"}]
NOTIFY = {"recipients": ["oncall@example.com"], "when": "always"}


def fixture_report(name: str) -> AnalysisReport:
    return AnalysisReport.model_validate_json((REPO / f"fixtures/reports/{name}.json").read_text())


def fixture_job(**update: Any) -> AnalysisJob:
    job = AnalysisJob.model_validate_json((REPO / "fixtures/jobs/job_completed.json").read_text())
    return job.model_copy(update=update)


def summary_of(name: str) -> ReportSummary:
    return fixture_report(name).summary()


def outbox(tmp_path: Path) -> list[EmailMessage]:
    files = sorted((tmp_path / "outbox").glob("*.eml")) if (tmp_path / "outbox").exists() else []
    return [email.message_from_bytes(f.read_bytes(), policy=email.policy.default) for f in files]


def body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain",))
    assert part is not None
    return str(part.get_content())


# --- contract ---------------------------------------------------------------------------------


def test_recipients_are_stripped_and_deduplicated() -> None:
    notify = EmailNotification(recipients=[" a@example.com", "A@Example.com", "b@example.org"])

    assert notify.recipients == ["a@example.com", "b@example.org"]
    assert notify.when is NotifyWhen.PROBLEMS
    assert notify.min_severity is Severity.HIGH


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
        EmailNotification(recipients=[address])


# --- policy -----------------------------------------------------------------------------------


def test_problems_policy_sends_on_severity_failure_or_partial() -> None:
    summary = summary_of("report_anomalies")
    medium = [f for f in summary.findings if f.severity is Severity.MEDIUM]
    anomalies = summary.model_copy(update={"findings": medium})
    at_medium = EmailNotification(recipients=["a@example.com"], min_severity=Severity.MEDIUM)
    stricter = EmailNotification(recipients=["a@example.com"], min_severity=Severity.HIGH)
    completed = fixture_job()

    assert medium
    assert skip_reason(at_medium, completed, anomalies) is None
    assert skip_reason(stricter, completed, anomalies) == "no finding at or above high severity"
    assert skip_reason(stricter, fixture_job(state=JobState.PARTIAL), anomalies) is None
    assert skip_reason(stricter, fixture_job(state=JobState.FAILED), None) is None


def test_always_policy_sends_reports_without_findings() -> None:
    notify = EmailNotification(recipients=["a@example.com"], when=NotifyWhen.ALWAYS)

    assert skip_reason(notify, fixture_job(), summary_of("report_healthy")) is None


# --- content ----------------------------------------------------------------------------------


def test_report_with_findings_lists_them_by_severity_and_links() -> None:
    summary = summary_of("report_anomalies")
    link = "https://assistant.example.com/reports/abc"

    rendered = render_email("shop", fixture_job(), summary, timezone="Europe/Berlin", link=link)

    top = ranked(summary.findings)[0]
    assert rendered.subject.startswith("[DevOps Assistant] shop: ")
    assert top.title in rendered.text
    assert rendered.text.index(top.title) < rendered.text.index("Open the report")
    assert f"Open the report: {link}" in rendered.text
    assert f'href="{link}"' in rendered.html
    assert "CET" in rendered.text or "CEST" in rendered.text


def test_report_without_findings_never_reads_as_healthy_when_signals_were_skipped() -> None:
    partial = render_email(
        "shop", fixture_job(state=JobState.PARTIAL), summary_of("report_partial_source_error")
    )
    healthy = render_email("shop", fixture_job(), summary_of("report_healthy"))

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

    failed_email = render_email("shop", failed, None)
    partial_email = render_email("shop", partial, summary_of("report_partial_source_error"))

    assert failed_email.subject == "[DevOps Assistant] shop: analysis failed"
    assert "Analysis failed: connection refused" in failed_email.text
    assert "(partial)" in partial_email.subject
    assert "Not included:" in partial_email.text


def test_report_values_are_escaped_and_never_break_headers() -> None:
    summary = summary_of("report_anomalies")
    finding = summary.findings[0].model_copy(update={"title": "<script>alert(1)</script>"})
    hostile = summary.model_copy(update={"findings": [finding]})

    rendered = render_email("shop\r\nBcc: x@example.com", fixture_job(), hostile)
    message = build_message("from@example.com", ["to@example.com"], rendered)

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "\n" not in rendered.subject
    assert message["Bcc"] is None


# --- delivery ---------------------------------------------------------------------------------


def make_client(tmp_path: Path, **config: Any) -> TestClient:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake", **config)
    return TestClient(create_app(settings))


def scheduled_project(client: TestClient, due: datetime, notify: dict[str, Any] | None) -> str:
    schedule: dict[str, Any] = {"time": due.strftime("%H:%M"), "timezone": "UTC"}
    if notify is not None:
        schedule["notify"] = notify
    res = client.post(
        "/api/projects",
        json={
            "name": "shop",
            "matchers": MATCHERS,
            "sources": [{"kind": "prometheus", "url": "synthetic://incident"}],
            "schedule": schedule,
        },
    )
    assert res.status_code == 201, res.text
    return str(res.json()["project_id"])


def run_scheduled(client: TestClient) -> dict[str, Any]:
    """Tick the scheduler for a due run and wait until the job and its email are done."""
    now = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2)
    services = client.app.state.services  # type: ignore[attr-defined]
    [analysis_id] = client.portal.call(services.scheduler.tick, now)  # type: ignore[union-attr]
    wait_idle(client)
    job: dict[str, Any] = client.get(f"/api/analyses/{analysis_id}").json()
    return job


def wait_idle(client: TestClient) -> None:
    """Until every submitted job has finished, including its email."""
    runner = client.app.state.services.runner  # type: ignore[attr-defined]
    assert client.portal is not None
    client.portal.call(runner.idle)


def due_time() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=2, hours=-1)


def test_scheduled_report_is_emailed_and_recorded(tmp_path: Path) -> None:
    with make_client(
        tmp_path, email_provider="fake", app_public_url="https://assistant.example.com/"
    ) as client:
        scheduled_project(client, due_time(), NOTIFY)

        job = run_scheduled(client)

        assert job["trigger"] == "scheduled"
        assert job["state"] in ("completed", "partial")
        assert job["notification"]["status"] == "sent"
        assert job["notification"]["recipients"] == 1
        [message] = outbox(tmp_path)
        assert message["To"] == "oncall@example.com"
        assert message["Subject"].startswith("[DevOps Assistant] shop: ")
        assert message["Auto-Submitted"] == "auto-generated"
        link = f"https://assistant.example.com/reports/{job['analysis_id']}"
        assert link in body_text(message)


def test_manual_analyses_and_projects_without_recipients_send_nothing(tmp_path: Path) -> None:
    with make_client(tmp_path, email_provider="fake") as client:
        pid = scheduled_project(client, due_time(), None)

        job = run_scheduled(client)
        submitted = client.post("/api/analyses", json={"project_id": pid}).json()["analysis"]
        wait_idle(client)
        manual = client.get(f"/api/analyses/{submitted['analysis_id']}").json()

        assert job["notification"] is None
        assert manual["notification"] is None
        assert outbox(tmp_path) == []


def test_unconfigured_email_is_recorded_not_failed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        scheduled_project(client, due_time(), NOTIFY)

        job = run_scheduled(client)

        assert job["state"] in ("completed", "partial")
        assert job["notification"]["status"] == "not_configured"
        assert client.get("/api/config").json()["email_configured"] is False


def test_delivery_failure_never_changes_the_job(tmp_path: Path) -> None:
    class Broken:
        async def send(self, message: EmailMessage) -> None:
            raise smtplib.SMTPRecipientsRefused({"oncall@example.com": (550, b"no such user")})

    with make_client(tmp_path, email_provider="fake") as client:
        client.app.state.services.notifier.transport = Broken()  # type: ignore[attr-defined]
        scheduled_project(client, due_time(), NOTIFY)

        job = run_scheduled(client)

        assert job["state"] in ("completed", "partial")
        assert job["report_available"] is True
        assert job["notification"]["status"] == "failed"
        assert job["notification"]["reason"].startswith("SMTPRecipientsRefused")


def test_policy_skip_is_recorded(tmp_path: Path) -> None:
    notify = {"recipients": ["oncall@example.com"], "when": "problems", "min_severity": "critical"}
    with make_client(tmp_path, email_provider="fake") as client:
        scheduled_project(client, due_time(), notify)

        job = run_scheduled(client)

        status = job["notification"]["status"]
        assert status in ("sent", "skipped")
        assert (status == "sent") == (
            job["state"] != "completed" or bool(job["finding_counts"]["critical"])
        )
        assert len(outbox(tmp_path)) == (1 if status == "sent" else 0)


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


# --- API and settings -------------------------------------------------------------------------


def test_test_email_endpoint(tmp_path: Path) -> None:
    with make_client(tmp_path / "off") as client:
        res = client.post("/api/notifications/test-email", json={"recipients": ["a@example.com"]})

        assert res.status_code == 409
        assert res.json()["code"] == "email_not_configured"

    with make_client(tmp_path, email_provider="fake") as client:
        res = client.post(
            "/api/notifications/test-email", json={"recipients": ["a@example.com", "A@example.com"]}
        )
        bad = client.post("/api/notifications/test-email", json={"recipients": ["nope"]})

        assert res.status_code == 200, res.text
        assert res.json()["recipients"] == ["a@example.com"]
        assert [m["Subject"] for m in outbox(tmp_path)] == ["[DevOps Assistant] Test email"]
        assert bad.status_code == 422
        assert client.get("/api/config").json()["email_configured"] is True


def test_schedule_notify_round_trips(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        scheduled_project(client, due_time(), {"recipients": ["B@example.com", "b@example.com"]})

        [project] = client.get("/api/projects").json()["items"]

        assert project["schedule"]["notify"] == {
            "recipients": ["B@example.com"],
            "when": "problems",
            "min_severity": "high",
        }


def test_email_settings_are_validated() -> None:
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
    assert (
        load_settings(_env_file=None, app_public_url="https://x.example.com/").app_public_url
        == "https://x.example.com"
    )
