"""Build identity of the running app and whether it is the latest published build."""

from enum import StrEnum

from pydantic import Field

from app.domain.common import Contract, UtcDatetime


class UpdateState(StrEnum):
    LATEST = "latest"
    """The build commit is the head of the tracked branch."""
    OUTDATED = "outdated"
    """The tracked branch has commits the build does not."""
    AHEAD = "ahead"
    """The build has commits not on the tracked branch (local development)."""
    UNKNOWN = "unknown"
    """The build commit is unknown, or the check failed (see reason)."""
    DISABLED = "disabled"
    """UPDATE_CHECK=false."""


class UpdateStatus(Contract):
    state: UpdateState
    branch: str
    commits_behind: int | None = Field(
        default=None, ge=0, description="Commits on the branch the build does not have."
    )
    compare_url: str | None = Field(
        default=None, description="Web page listing the commits between the build and the branch."
    )
    checked_at: UtcDatetime | None = None
    reason: str | None = Field(default=None, description="Why the state is unknown.")


class VersionInfo(Contract):
    """GET /api/version — the running build and its update status."""

    version: str
    commit: str | None = Field(description="Full git commit SHA of the build, when known.")
    built_at: UtcDatetime | None
    repository_url: str
    update: UpdateStatus
