"""What the Grafana source needs from Grafana, as typed results.

``GrafanaClient`` implements it over Grafana's HTTP API; ``SyntheticGrafanaApi`` generates
demo data. Times are epoch seconds.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class AlertInstance:
    """A current alert instance of a rule (rules API)."""

    labels: dict[str, str]
    state: str
    active_at: int | None = None


@dataclass(frozen=True)
class AlertRule:
    uid: str
    title: str
    folder_uid: str
    folder: str
    """Folder title."""
    group: str
    labels: dict[str, str] = field(default_factory=dict)
    paused: bool = False
    instances: list[AlertInstance] = field(default_factory=list)


@dataclass(frozen=True)
class Transition:
    """One state change of one alert instance (state history)."""

    id: int
    time: int
    prev: str
    next: str
    instance: str | None
    """Canonical label text ``{a=1, b=2}``; None when the history text has no label set."""
    labels: dict[str, str]


@dataclass(frozen=True)
class History:
    """State changes of one rule in [start, end), oldest first."""

    transitions: list[Transition]
    truncated: bool = False
    """More changes than ``MAX_TRANSITIONS_PER_RULE``; the oldest are missing."""
    known_from: int | None = None
    """States before this time are unknown (truncated or not retained); None: whole window."""


class GrafanaApi(Protocol):
    @property
    def base_url(self) -> str: ...

    @property
    def backend(self) -> str | None: ...

    def begin(self, end_time: datetime) -> None:
        """Called once per analysis or probe with its frozen end time."""
        ...

    async def version(self) -> str | None: ...

    async def rules(self) -> list[AlertRule]:
        """Every Grafana-managed alert rule of the organization with its current instances."""
        ...

    async def history(self, rule: AlertRule, start: int, end: int) -> History:
        """The rule's state changes in [start, end)."""
        ...

    async def aclose(self) -> None: ...
