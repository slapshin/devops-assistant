"""Signal definition model and PromQL template builders shared by the catalog modules."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.common import EntityKind, SignalFamily, Unit
from app.metrics.promql import QueryTemplate


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


class Role(StrEnum):
    SIGNAL = "signal"
    """Analysed directly."""
    OPERAND = "operand"
    """Numerator/denominator for a ratio derived in the analysis."""


@dataclass(frozen=True)
class Gate:
    """Instant query that must return a positive value for the signal to be supported."""

    template: QueryTemplate
    unsupported_reason: str


@dataclass(frozen=True)
class SignalDef:
    signal: str
    family: SignalFamily
    unit: Unit
    entity_kind: EntityKind
    identity: tuple[str, ...]
    query: QueryTemplate
    required_metrics: tuple[str, ...] = ()
    """Metrics that must be present for the signal to be supported; defaults to the query's."""
    direction: Direction = Direction.UP
    role: Role = Role.SIGNAL
    gates: tuple[Gate, ...] = ()
    traffic: str | None = None
    """Request-count signal that ranks this signal's routes (top N kept, the rest summed)."""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.required_metrics:
            object.__setattr__(self, "required_metrics", self.query.metrics)

    @property
    def route_level(self) -> bool:
        return self.traffic is not None


# --- template builders ---------------------------------------------------------------------
# Templates are ``str.format`` strings (see promql.py): ``{s}`` is the scope, ``{w}`` the window,
# and literal braces are doubled. These helpers keep that escaping in one place.


def q(template: str, *metrics: str) -> QueryTemplate:
    return QueryTemplate(template, metrics)


def sel(metric: str, *matchers: str) -> str:
    """Scoped selector, e.g. ``metric{{{s}, mode="idle"}}``."""
    body = ", ".join(("{s}", *matchers))
    return f"{metric}{{{{{body}}}}}"


def rate(metric: str, *matchers: str, fn: str = "rate") -> str:
    return f"{fn}({sel(metric, *matchers)}[{{w}}])"


def aggregate(agg: str, by: tuple[str, ...], expr: str) -> str:
    return f"{agg} by ({', '.join(by)}) ({expr})"


def sum_rate(metric: str, by: tuple[str, ...], *matchers: str, fn: str = "rate") -> str:
    return aggregate("sum", by, rate(metric, *matchers, fn=fn))


def used_ratio(free: str, total: str, by: tuple[str, ...], *matchers: str) -> str:
    """``1 - free / total``, skipping entities whose total is 0."""
    free_by = aggregate("max", by, sel(free, *matchers))
    total_by = aggregate("max", by, sel(total, *matchers) + " > 0")
    return f"1 - {free_by} / {total_by}"


def quantile(phi: float, bucket: str, by: tuple[str, ...]) -> str:
    return f"histogram_quantile({phi}, {aggregate('sum', (*by, 'le'), rate(bucket))})"


def bucket_gate(bucket: str) -> Gate:
    return Gate(
        q(f"count({sel(bucket)})", bucket),
        f"No histogram buckets for {bucket.removesuffix('_bucket')}; percentiles unavailable.",
    )


def latency_quantiles(
    prefix: str,
    kind: EntityKind,
    identity: tuple[str, ...],
    bucket: str,
    traffic: str,
    phis: tuple[float, ...] = (0.95, 0.99),
) -> tuple[SignalDef, ...]:
    gate = bucket_gate(bucket)
    return tuple(
        SignalDef(
            f"{prefix}_latency_p{int(phi * 100)}",
            SignalFamily.LATENCY,
            Unit.SECONDS,
            kind,
            identity,
            q(quantile(phi, bucket, identity), bucket),
            gates=(gate,),
            traffic=traffic,
        )
        for phi in phis
    )
