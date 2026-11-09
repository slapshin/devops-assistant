"""Build identity and the latest-build check (GitHub compare API, mocked)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.domain.version import UpdateState
from app.main import create_app
from app.settings import ConfigError, Settings, load_settings
from app.version import CHECKED_TTL_SECONDS, FAILED_TTL_SECONDS, UpdateChecker, git_commit

COMMIT = "9bfe484" + "0" * 33
Handler = Callable[[httpx.Request], httpx.Response]


def settings(**kw: object) -> Settings:
    return load_settings(_env_file=None, **kw)


def compare(status: str, ahead_by: int = 0, behind_by: int = 0) -> Handler:
    body = {"status": status, "ahead_by": ahead_by, "behind_by": behind_by, "commits": []}
    return lambda _: httpx.Response(200, json=body)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def checker(
    handler: Handler,
    calls: list[httpx.Request] | None = None,
    commit: str | None = COMMIT,
    clock: Clock | None = None,
    **kw: object,
) -> UpdateChecker:
    def recording(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return UpdateChecker(settings(**kw), commit, httpx.MockTransport(recording), clock or Clock())


async def test_head_of_branch_is_latest() -> None:
    calls: list[httpx.Request] = []

    status = await checker(compare("identical"), calls).status()

    assert status.state is UpdateState.LATEST and status.commits_behind == 0
    assert status.compare_url == (
        f"https://github.com/slapshin/devops-assistant/compare/{COMMIT}...main"
    )
    assert str(calls[0].url) == (
        f"https://api.github.com/repos/slapshin/devops-assistant/compare/{COMMIT}...main?per_page=1"
    )


@pytest.mark.parametrize(
    ("github_status", "state"),
    [("ahead", UpdateState.OUTDATED), ("diverged", UpdateState.OUTDATED)],
)
async def test_newer_branch_commits_make_the_build_outdated(
    github_status: str, state: UpdateState
) -> None:
    status = await checker(compare(github_status, ahead_by=3, behind_by=1)).status()

    assert status.state is state and status.commits_behind == 3


async def test_unpublished_local_commits_are_ahead() -> None:
    status = await checker(compare("behind", behind_by=2)).status()

    assert status.state is UpdateState.AHEAD and status.commits_behind == 0


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(404), "The build commit is not on GitHub (slapshin/devops-assistant)."),
        (httpx.Response(403), "GitHub rate limit reached; the check is retried later."),
        (httpx.Response(500), "GitHub answered HTTP 500."),
        (httpx.Response(200, json={"status": "odd"}), "GitHub returned an unexpected answer."),
        (httpx.Response(200, text="<html>"), "GitHub returned an unexpected answer."),
    ],
)
async def test_failed_checks_are_unknown_with_a_reason(
    response: httpx.Response, reason: str
) -> None:
    status = await checker(lambda _: response).status()

    assert status.state is UpdateState.UNKNOWN and status.reason == reason
    assert status.commits_behind is None


async def test_unreachable_github_is_unknown() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    status = await checker(fail).status()

    assert status.state is UpdateState.UNKNOWN and status.reason == "GitHub is unreachable."


async def test_disabled_or_unknown_commit_never_calls_github() -> None:
    calls: list[httpx.Request] = []

    disabled = await checker(compare("identical"), calls, update_check=False).status()
    no_commit = await checker(compare("identical"), calls, commit=None).status()

    assert disabled.state is UpdateState.DISABLED
    assert no_commit.state is UpdateState.UNKNOWN
    assert no_commit.reason == "The build commit is unknown."
    assert calls == []


async def test_answers_are_cached_and_failures_retried_sooner() -> None:
    calls: list[httpx.Request] = []
    responses = [httpx.Response(500), *[compare("identical")(httpx.Request("GET", "/"))] * 2]
    clock = Clock()
    subject = checker(lambda _: responses[len(calls) - 1], calls, clock=clock)

    first = await subject.status()
    await subject.status()
    clock.now += FAILED_TTL_SECONDS
    second = await subject.status()
    clock.now += CHECKED_TTL_SECONDS - 1
    third = await subject.status()

    assert first.state is UpdateState.UNKNOWN
    assert second.state is UpdateState.LATEST and third == second
    assert len(calls) == 2


async def test_custom_repository_and_branch() -> None:
    calls: list[httpx.Request] = []
    subject = checker(
        compare("identical"),
        calls,
        update_check_repository="acme/fork",
        update_check_branch="release/1.x",
        github_api_url="https://ghe.example.com/api/v3/",
    )

    info = await subject.version()

    assert info.repository_url == "https://github.com/acme/fork"
    assert info.update.branch == "release/1.x"
    assert calls[0].url.path == f"/api/v3/repos/acme/fork/compare/{COMMIT}...release/1.x"


def test_build_info_settings_validation() -> None:
    built = settings(app_commit=COMMIT.upper(), app_build_date="2026-11-09T10:00:00Z")

    assert built.app_commit == COMMIT and built.app_build_date is not None
    assert settings(app_commit="", app_build_date="").app_commit is None
    for bad in (
        {"app_commit": "9bfe484"},
        {"update_check_repository": "x"},
        {"update_check_branch": "a..b"},
    ):
        with pytest.raises(ConfigError):
            settings(**bad)


def test_git_commit_outside_a_checkout_is_none(tmp_path: Path) -> None:
    assert git_commit(tmp_path) is None


def test_version_endpoint(tmp_path: Path) -> None:
    app = create_app(
        settings(
            data_dir=tmp_path,
            ai_provider="fake",
            app_commit=COMMIT,
            app_build_date="2026-11-09T10:00:00+02:00",
        )
    )
    app.state.services.update_checker.transport = httpx.MockTransport(compare("ahead", ahead_by=4))

    with TestClient(app) as client:
        body: dict[str, Any] = client.get("/api/version").json()

    assert body["version"] == __version__ and body["commit"] == COMMIT
    assert body["built_at"] == "2026-11-09T08:00:00Z"
    assert body["update"]["state"] == "outdated" and body["update"]["commits_behind"] == 4
