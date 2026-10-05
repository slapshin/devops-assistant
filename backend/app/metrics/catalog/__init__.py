"""Versioned catalog of scoped signal queries (T004).

Signals are defined per source in the sibling modules and assembled into `CATALOG` here.

Conventions:
- ``rate``/``increase`` are applied to each raw counter before any aggregation, so counter
  resets are handled per series by the source.
- Ratios are returned only where their operands are the same entity; HTTP/RPC failure ratios
  are *not* computed in PromQL: numerator and denominator series are collected separately so
  the analysis can apply the minimum-volume rule and treat an absent numerator as zero only
  where the denominator was observed.
- Exclusion rules (pseudo filesystems, virtual devices, unnamed cgroups) are constants in the
  source modules, documented in docs/metrics-catalog.md.
"""

from app.metrics.catalog.base import Direction, Gate, Role, SignalDef
from app.metrics.catalog.container import CONTAINER_CATALOG
from app.metrics.catalog.host import HOST_CATALOG
from app.metrics.catalog.mysql import MYSQL_CATALOG
from app.metrics.catalog.postgres import POSTGRES_CATALOG
from app.metrics.catalog.proxy import PROXY_CATALOG
from app.metrics.catalog.redis import REDIS_CATALOG
from app.metrics.catalog.requests import REQUEST_CATALOG

__all__ = [
    "ALL_REQUIRED_METRICS",
    "BY_SIGNAL",
    "CATALOG",
    "CATALOG_VERSION",
    "TRAFFIC_SIGNALS",
    "Direction",
    "Gate",
    "Role",
    "SignalDef",
]

CATALOG_VERSION = "catalog-2026.10.5"

CATALOG: tuple[SignalDef, ...] = (
    *HOST_CATALOG,
    *CONTAINER_CATALOG,
    *REQUEST_CATALOG,
    *PROXY_CATALOG,
    *POSTGRES_CATALOG,
    *MYSQL_CATALOG,
    *REDIS_CATALOG,
)
TRAFFIC_SIGNALS = frozenset(d.traffic for d in CATALOG if d.traffic is not None)

BY_SIGNAL = {d.signal: d for d in CATALOG}
if len(BY_SIGNAL) != len(CATALOG):
    raise ValueError("duplicate signal names in the metrics catalog")
ALL_REQUIRED_METRICS = sorted({m for d in CATALOG for m in d.required_metrics})
