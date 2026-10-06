"""What the Wazuh source needs from the indexer, as typed results.

``WazuhClient`` implements it over the indexer's search API; ``SyntheticWazuhApi`` generates
demo data. Counts are per 5-minute bucket (the bucket's start in epoch seconds).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class AgentInfo:
    name: str
    alerts: int
    """Alerts in the discovery window."""
    first_seen: int
    """Epoch seconds of the agent's first alert in the discovery window."""


@dataclass(frozen=True)
class AgentList:
    agents: list[AgentInfo]
    """Agents with alerts, in name order, at most ``MAX_AGENTS``."""
    truncated: bool = False
    """More agents matched than are analysed."""


@dataclass(frozen=True)
class GroupMembers:
    """Agent names of each configured group (groups without members map to [])."""

    members: dict[str, list[str]]
    truncated: bool = False
    """A group has more than ``MAX_GROUP_MEMBERS`` agents."""

    def names(self) -> list[str]:
        return sorted({name for names in self.members.values() for name in names})


@dataclass
class Chunk:
    """Counts of one request over [start, end): signal -> agent -> bucket start -> count."""

    query: str
    values: dict[str, dict[str, dict[int, float]]] = field(default_factory=dict)


class WazuhApi(Protocol):
    @property
    def base_url(self) -> str: ...

    @property
    def backend(self) -> str | None: ...

    def begin(self, end_time: datetime) -> None:
        """Called once per analysis or probe with its frozen end time."""
        ...

    async def version(self) -> str | None:
        """The indexer's version when the user may read it, else None."""
        ...

    async def group_members(self, groups: Sequence[str], start: int, end: int) -> GroupMembers:
        """Agents in each group in monitoring snapshots of [start, end)."""
        ...

    async def agents(self, names: Sequence[str] | None, start: int, end: int) -> AgentList:
        """Agents with alerts in [start, end): those in ``names`` (None: any name), narrowed by
        the configured labels."""
        ...

    async def series(self, agents: Sequence[str], start: int, end: int) -> Chunk:
        """Per-agent 5-minute counts of every signal in [start, end)."""
        ...

    async def aclose(self) -> None: ...
