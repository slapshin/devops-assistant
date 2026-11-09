"""Build identity and the "is this the latest build?" check against GitHub.

The published image (:latest) follows the tracked branch, so the build is the latest when its
commit is the branch head. The check compares the two with the GitHub compare API and caches the
answer, so the API's unauthenticated rate limit (60 requests per hour) is never a concern.
"""

import asyncio
import logging
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app import __version__
from app.domain.version import UpdateState, UpdateStatus, VersionInfo
from app.settings import GIT_SHA, Settings

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_TIMEOUT_SECONDS = 2
TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 3.0
CHECKED_TTL_SECONDS = 6 * 3600
"""How long a successful answer is reused."""
FAILED_TTL_SECONDS = 15 * 60
"""How long a failed check waits before it is retried."""
USER_AGENT = f"devops-assistant/{__version__}"

# GitHub compare base...head, with base = build and head = branch, says how the branch relates
# to the build: "ahead" means the branch has newer commits.
_COMPARE_STATES = {
    "identical": UpdateState.LATEST,
    "ahead": UpdateState.OUTDATED,
    "diverged": UpdateState.OUTDATED,
    "behind": UpdateState.AHEAD,
}


def git_commit(root: Path = REPO_ROOT) -> str | None:
    """HEAD of a source checkout (native runs); None outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and GIT_SHA.fullmatch(commit) else None


class UpdateChecker:
    def __init__(
        self,
        settings: Settings,
        commit: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.commit = commit
        self.transport = transport
        self.clock = clock
        self._cached: tuple[float, UpdateStatus] | None = None
        self._lock = asyncio.Lock()

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.settings.update_check_repository}"

    def info(self, update: UpdateStatus) -> VersionInfo:
        return VersionInfo(
            version=__version__,
            commit=self.commit,
            built_at=self.settings.app_build_date,
            repository_url=self.repository_url,
            update=update,
        )

    async def version(self) -> VersionInfo:
        return self.info(await self.status())

    async def status(self) -> UpdateStatus:
        branch = self.settings.update_check_branch
        if not self.settings.update_check:
            return UpdateStatus(state=UpdateState.DISABLED, branch=branch)
        if self.commit is None:
            return UpdateStatus(
                state=UpdateState.UNKNOWN, branch=branch, reason="The build commit is unknown."
            )

        async with self._lock:
            if self._cached is not None:
                expires, status = self._cached
                if self.clock() < expires:
                    return status
            status = await self._check(self.commit, branch)
            ttl = FAILED_TTL_SECONDS if status.state is UpdateState.UNKNOWN else CHECKED_TTL_SECONDS
            self._cached = (self.clock() + ttl, status)
            return status

    async def _check(self, commit: str, branch: str) -> UpdateStatus:
        repo = self.settings.update_check_repository
        url = f"{self.settings.github_api_url}/repos/{repo}/compare/{commit}...{branch}"
        now = datetime.now(UTC)

        def unknown(reason: str) -> UpdateStatus:
            return UpdateStatus(
                state=UpdateState.UNKNOWN, branch=branch, checked_at=now, reason=reason
            )

        timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as http:
                # per_page=1: only the counts are needed, not the commit list.
                response = await http.get(url, params={"per_page": 1}, headers=headers)
        except httpx.HTTPError as exc:
            log.info("update check failed: %s", type(exc).__name__)
            return unknown("GitHub is unreachable.")

        if response.status_code == httpx.codes.NOT_FOUND:
            return unknown(f"The build commit is not on GitHub ({repo}).")
        if response.status_code in (httpx.codes.FORBIDDEN, httpx.codes.TOO_MANY_REQUESTS):
            return unknown("GitHub rate limit reached; the check is retried later.")
        if response.status_code != httpx.codes.OK:
            return unknown(f"GitHub answered HTTP {response.status_code}.")

        try:
            body = response.json()
            state = _COMPARE_STATES[body["status"]]
            behind = int(body["ahead_by"])
        except ValueError, KeyError, TypeError:
            return unknown("GitHub returned an unexpected answer.")

        return UpdateStatus(
            state=state,
            branch=branch,
            commits_behind=max(behind, 0),
            compare_url=f"https://github.com/{repo}/compare/{commit}...{branch}",
            checked_at=now,
        )


def update_checker(settings: Settings) -> UpdateChecker:
    return UpdateChecker(settings, settings.app_commit or git_commit())
