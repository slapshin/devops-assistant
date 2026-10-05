"""Rule model and value formatting shared by every rule category."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.common import Severity, SignalFamily, Unit


class RuleKind(StrEnum):
    LEVEL = "level"
    """Relative robust baseline plus optional absolute heuristic."""
    EVENT = "event"
    """Any positive count in a step is anomalous (OOM kills, new task failures)."""
    SHORTFALL = "shortfall"
    """Value >= 1 sustained for shortfall_min_minutes (missing replicas)."""


class Dir(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


@dataclass(frozen=True)
class Rule:
    name: str
    family: SignalFamily
    label: str
    kind: RuleKind = RuleKind.LEVEL
    thresholds: str | None = None
    direction: Dir = Dir.UP
    volume_guard: bool = False
    volume_noun: str = "requests"
    """What the guarded volume counts, for confidence reasons."""
    severity_cap: Severity | None = None
    event_points: int = 0
    title_up: str | None = None
    title_down: str | None = None

    def title(self, *, up: bool, threshold: float | None, unit: Unit) -> str:
        if threshold is not None and self.kind is RuleKind.LEVEL:
            return f"{self.label} at or above {format_value(threshold, unit)} (heuristic)"
        if self.kind is not RuleKind.LEVEL:
            return self.title_up or self.label
        custom = self.title_up if up else self.title_down
        return custom or f"{self.label} {'above' if up else 'below'} expected range"


SUB_MILLISECOND = 0.001
"""Below this, durations are shown in µs (Redis commands run in microseconds)."""


def format_value(value: float, unit: Unit) -> str:
    match unit:
        case Unit.RATIO:
            return f"{value * 100:.1f} %"
        case Unit.SECONDS:
            if 0 < value < SUB_MILLISECOND:
                return f"{value * 1e6:.0f} µs"
            return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"
        case Unit.BYTES:
            return _bytes(value)
        case Unit.BYTES_PER_SECOND:
            return f"{_bytes(value)}/s"
        case Unit.REQUESTS_PER_SECOND:
            return f"{value:.2f} req/s"
        case Unit.PER_SECOND:
            return f"{value:.3g}/s"
        case _:
            return f"{value:.3g}"


def _bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
