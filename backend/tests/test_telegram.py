"""Telegram notifications (T027): rendering, Bot API client, driver, and API."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain.common import Severity
from app.domain.jobs import AnalysisJob, ErrorCode, JobError, JobState
from app.domain.notifications import (
    NotificationPolicy,
    NotifyWhen,
    TelegramDestination,
)
from app.domain.report import AnalysisReport, ReportSummary
from app.main import create_app
from app.notifications.drivers.base import DeliveryError
from app.notifications.drivers.telegram.client import BotApiClient, TelegramApiError
from app.notifications.drivers.telegram.driver import TelegramDriver
from app.notifications.drivers.telegram.render import (
    HEALTHY_MARKER,
    MAX_MESSAGE_UNITS,
    UNCLEAR_MARKER,
    render_telegram,
    utf16_units,
)
from app.notifications.message import ReportMessage, build_message
from app.notifications.policy import has_problem
from app.settings import load_settings

REPO = Path(__file__).resolve().parents[2]
TOKEN = "123456:AAH-test-token-0123456789abcdefghij"
BASE = "https://api.telegram.test"
CHAT = TelegramDestination(chat_id="-1001000000001", message_thread_id=3, bot_token_set=True)
SECRETS = {"bot_token": TOKEN}

Handler = Callable[[httpx.Request], httpx.Response]


def summary_of(name: str) -> ReportSummary:
    path = REPO / f"fixtures/reports/{name}.json"
    return AnalysisReport.model_validate_json(path.read_text()).summary()


def fixture_job(**update: Any) -> AnalysisJob:
    job = AnalysisJob.model_validate_json((REPO / "fixtures/jobs/job_completed.json").read_text())
    return job.model_copy(update=update)


def message(name: str = "report_anomalies", **job: Any) -> ReportMessage:
    return build_message(
        "shop", fixture_job(**job), summary_of(name), link="https://x.example.com/reports/1"
    )


def ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def error(status: int, description: str, **parameters: Any) -> httpx.Response:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return httpx.Response(status, json=body)


def driver_with(handler: Handler, calls: list[httpx.Request] | None = None) -> TelegramDriver:
    def recording(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording)
    return TelegramDriver(lambda token: BotApiClient(BASE, token, transport))


async def deliver(driver: TelegramDriver, msg: ReportMessage, *, silent: bool = False) -> int:
    return await driver.deliver(CHAT, SECRETS, msg, silent=silent)


# --- rendering --------------------------------------------------------------------------------


def test_report_renders_as_escaped_html_with_findings_and_link() -> None:
    summary = summary_of("report_anomalies")
    finding = summary.findings[0].model_copy(update={"title": "5xx <b>&</b> spikes"})
    hostile = summary.model_copy(update={"findings": [finding]})

    text = render_telegram(
        build_message("shop <&>", fixture_job(), hostile, link="https://x.example.com/r?a=1&b=2")
    )

    assert text.split(" ", 1)[1].startswith(
        f"<b>shop &lt;&amp;&gt;</b>: 1 {finding.severity.value}"
    )
    assert "5xx &lt;b&gt;&amp;&lt;/b&gt; spikes" in text
    assert "<b>&</b>" not in text
    assert '<a href="https://x.example.com/r?a=1&amp;b=2">Open the report</a>' in text


def test_healthy_marker_only_when_everything_was_evaluated() -> None:
    healthy = render_telegram(message("report_healthy"))
    partial = render_telegram(message("report_partial_source_error", state=JobState.PARTIAL))
    failed_job = fixture_job(
        state=JobState.FAILED,
        report_available=False,
        error=JobError(code=ErrorCode.JOB_TIMEOUT, message="took too long"),
    )
    failed = render_telegram(build_message("shop", failed_job, None))

    assert healthy.startswith(HEALTHY_MARKER)
    assert "No anomalies found in the evaluated signals." in healthy
    assert partial.startswith(UNCLEAR_MARKER)
    assert "does not mean healthy" in partial
    assert "Partial:" in partial
    assert failed.startswith("❗")
    assert "Analysis failed: took too long" in failed


def test_ai_summary_is_marked_unverified() -> None:
    text = render_telegram(message("report_anomalies"))

    assert "<blockquote expandable><b>AI summary (unverified)</b>" in text


def test_long_reports_are_shortened_to_the_message_limit() -> None:
    summary = summary_of("report_anomalies")
    long = [
        summary.findings[0].model_copy(update={"title": f"finding {i} " + "x" * 600})
        for i in range(10)
    ]
    big = summary.model_copy(update={"findings": long})

    text = render_telegram(
        build_message("shop", fixture_job(), big, link="https://x.example.com/reports/1")
    )

    assert utf16_units(text) <= MAX_MESSAGE_UNITS
    assert "finding 0 " in text
    assert "finding 9 " not in text
    assert "more" in text
    assert text.endswith('<a href="https://x.example.com/reports/1">Open the report</a>')


# --- Bot API client ---------------------------------------------------------------------------


async def test_send_message_parameters() -> None:
    calls: list[httpx.Request] = []
    driver = driver_with(lambda _: ok({"message_id": 1}), calls)

    reached = await deliver(driver, message("report_anomalies"))

    [request] = calls
    body = json.loads(request.content)
    assert reached == 1
    assert str(request.url) == f"{BASE}/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == -1001000000001
    assert body["message_thread_id"] == 3
    assert body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}
    assert body["disable_notification"] is False


async def test_reports_without_problems_are_sent_silently() -> None:
    calls: list[httpx.Request] = []
    driver = driver_with(lambda _: ok({"message_id": 1}), calls)
    always = NotificationPolicy(when=NotifyWhen.ALWAYS)
    critical = NotificationPolicy(when=NotifyWhen.ALWAYS, min_severity=Severity.CRITICAL)
    healthy = message("report_healthy")
    partial = message("report_partial_source_error", state=JobState.PARTIAL)

    await deliver(driver, healthy, silent=not has_problem(always, healthy))
    await deliver(driver, partial, silent=not has_problem(critical, partial))

    silent = [json.loads(c.content)["disable_notification"] for c in calls]
    assert silent == [True, False]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (error(401, "Unauthorized"), "Bot token rejected"),
        (error(400, "Bad Request: chat not found"), "Chat not found"),
        (error(403, "Forbidden: bot was blocked by the user"), "cannot post in this chat"),
        (error(403, "Forbidden: bot was kicked from the group chat"), "bot was kicked"),
        (
            error(400, "Bad Request: group chat was upgraded", migrate_to_chat_id=-1009),
            "set the chat ID to -1009",
        ),
        (error(400, "Bad Request: message thread not found"), "Topic not found"),
        (error(429, "Too Many Requests", retry_after=60), "Rate limited by Telegram for 60 s"),
        (error(500, "Internal Server Error"), "Telegram API error 500"),
        (httpx.Response(502, text="<html>bad gateway</html>"), "without a JSON body"),
    ],
)
async def test_api_errors_become_actionable_reasons(response: httpx.Response, reason: str) -> None:
    driver = driver_with(lambda _: response)

    with pytest.raises(DeliveryError) as raised:
        await deliver(driver, message())

    assert reason in raised.value.reason
    assert TOKEN not in raised.value.reason


async def test_rate_limits_are_retried_once_when_short(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("app.notifications.drivers.telegram.driver.asyncio.sleep", no_sleep)
    answers = [error(429, "Too Many Requests", retry_after=2), ok({"message_id": 1})]
    calls: list[httpx.Request] = []
    driver = driver_with(lambda _: answers.pop(0), calls)

    reached = await deliver(driver, message())

    assert reached == 1
    assert waits == [2]
    assert len(calls) == 2


async def test_network_errors_and_descriptions_never_leak_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    caplog.set_level(logging.DEBUG)
    refused = driver_with(refuse)
    echoing = driver_with(lambda _: error(400, f"Bad Request: token {TOKEN} is odd"))

    with pytest.raises(DeliveryError) as network:
        await deliver(refused, message())
    with pytest.raises(DeliveryError) as echoed:
        await deliver(echoing, message())

    assert network.value.reason == "Telegram API unreachable: ConnectError"
    assert TOKEN not in echoed.value.reason
    assert "[REDACTED]" in echoed.value.reason
    assert TOKEN not in caplog.text


async def test_a_missing_token_is_reported() -> None:
    driver = driver_with(lambda _: ok({}))

    with pytest.raises(DeliveryError, match="no bot token"):
        await driver.deliver(CHAT, {}, message(), silent=False)


async def test_chats_lists_recent_chats_newest_first() -> None:
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}}},
        {
            "update_id": 2,
            "my_chat_member": {
                "chat": {"id": -1001, "type": "supergroup", "title": "Ops", "is_forum": True}
            },
        },
        {
            "update_id": 3,
            "channel_post": {"chat": {"id": -1002, "type": "channel", "username": "news"}},
        },
        {"update_id": 4, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return ok({"id": 1, "is_bot": True, "username": "ops_bot"})
        assert json.loads(request.content) == {"limit": 100, "timeout": 0}
        return ok(updates)

    found = await driver_with(handler).chats(TOKEN)

    assert found.bot_username == "ops_bot"
    assert [(c.chat_id, c.title, c.type, c.is_forum) for c in found.items] == [
        ("42", "Ann", "private", False),
        ("-1002", "@news", "channel", False),
        ("-1001", "Ops", "supergroup", True),
    ]


async def test_webhook_conflict_is_explained() -> None:
    conflict = error(409, "Conflict: can't use getUpdates method while webhook is active")
    driver = driver_with(
        lambda r: ok({"username": "b"}) if r.url.path.endswith("/getMe") else conflict
    )

    with pytest.raises(TelegramApiError) as raised:
        await driver.chats(TOKEN)

    assert raised.value.error_code == 409
    assert "enter the chat ID" in raised.value.reason


# --- API --------------------------------------------------------------------------------------


def make_client(tmp_path: Path, **config: Any) -> TestClient:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake", **config)
    return TestClient(create_app(settings))


CHANNEL: dict[str, Any] = {
    "name": "Ops chat",
    "destination": {"kind": "telegram", "bot_token": TOKEN, "chat_id": "-1001000000001"},
}


def create_channel(client: TestClient) -> str:
    res = client.post("/api/notification-channels", json=CHANNEL)
    assert res.status_code == 201, res.text
    return str(res.json()["channel_id"])


def test_test_message_and_chat_lookup_through_the_fake_provider(tmp_path: Path) -> None:
    with make_client(tmp_path, telegram_provider="fake") as client:
        channel_id = create_channel(client)
        stored = {k: v for k, v in CHANNEL["destination"].items() if k != "bot_token"}

        tested = client.post(
            "/api/notification-channels/test",
            json={"channel_id": channel_id, "destination": stored},
        )
        drafted = client.post(
            "/api/notification-channels/test", json={"destination": CHANNEL["destination"]}
        )
        tokenless = client.post("/api/notification-channels/test", json={"destination": stored})
        chats = client.post(
            "/api/notification-channels/telegram/chats", json={"channel_id": channel_id}
        )
        by_token = client.post(
            "/api/notification-channels/telegram/chats", json={"bot_token": TOKEN}
        )
        nothing = client.post("/api/notification-channels/telegram/chats", json={})

        assert tested.status_code == 200, tested.text
        assert tested.json()["detail"] == "sent to Chat -1001000000001 (supergroup)"
        assert drafted.status_code == 200, drafted.text
        assert tokenless.status_code == 422
        assert tokenless.json()["errors"][0]["field"] == "body.destination.bot_token"
        assert chats.json()["bot_username"] == "demo_bot"
        assert chats.json()["items"][0]["title"] == "Ops (demo)"
        assert by_token.status_code == 200
        assert nothing.status_code == 422
        posts = [json.loads(p.read_text()) for p in (tmp_path / "outbox/telegram").glob("*.json")]
        assert len(posts) == 2
        assert all("test message" in p["text"] for p in posts)


def test_turned_off_telegram_is_reported(tmp_path: Path) -> None:
    with make_client(tmp_path, telegram_provider="none") as client:
        res = client.post("/api/notification-channels/telegram/chats", json={"bot_token": TOKEN})

        assert res.status_code == 409
        assert res.json()["code"] == "channel_not_configured"
