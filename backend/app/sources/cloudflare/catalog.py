"""Cloudflare signals and the GraphQL documents that fetch them.

Every document filters by zone, time range and (optionally) hostnames and groups by
``datetimeFiveMinutes``, so one row is one bucket of the analysis grid. Values are inlined:
the zone ID is 32 hex characters and hostnames are validated, so nothing needs escaping, and
the query text shown as evidence is exactly what was sent.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.common import SignalFamily, Unit
from app.sources.cloudflare.api import FIREWALL_DATASET, HTTP_DATASET

CATALOG_VERSION = "cloudflare-2026.10.6"
CHUNK_PLACEHOLDER = ("$start", "$end")


class Query(StrEnum):
    TRAFFIC = "traffic"
    TIMING = "timing"
    SECURITY = "security"


@dataclass(frozen=True)
class CloudflareSignal:
    signal: str
    family: SignalFamily
    unit: Unit
    query: Query
    fields: tuple[str, ...]
    description: str
    rate: bool = True
    """Counts per bucket, analysed per second; otherwise a value (a duration)."""

    @property
    def dataset(self) -> str:
        return FIREWALL_DATASET if self.query is Query.SECURITY else HTTP_DATASET


_EDGE, _SEC = SignalFamily.EDGE, SignalFamily.SECURITY
_RPS = Unit.REQUESTS_PER_SECOND
SIGNALS = (
    CloudflareSignal("cf_requests", _EDGE, _RPS, Query.TRAFFIC, ("count",), "Edge requests"),
    CloudflareSignal(
        "cf_5xx", _EDGE, _RPS, Query.TRAFFIC, ("count", "edgeResponseStatus"), "Edge 5xx"
    ),
    CloudflareSignal(
        "cf_52x",
        _EDGE,
        _RPS,
        Query.TRAFFIC,
        ("count", "edgeResponseStatus"),
        "Origin errors Cloudflare returned (520-530: origin down, timed out, TLS failure)",
    ),
    CloudflareSignal(
        "cf_404", _EDGE, _RPS, Query.TRAFFIC, ("count", "edgeResponseStatus"), "Edge 404"
    ),
    CloudflareSignal(
        "cf_4xx", _EDGE, _RPS, Query.TRAFFIC, ("count", "edgeResponseStatus"), "Edge 4xx"
    ),
    CloudflareSignal(
        "cf_cache_hits",
        _EDGE,
        _RPS,
        Query.TRAFFIC,
        ("count", "cacheStatus"),
        "Requests served from cache (hit, stale, updating, revalidated)",
    ),
    CloudflareSignal(
        "cf_ttfb_p95",
        _EDGE,
        Unit.SECONDS,
        Query.TIMING,
        ("quantiles.edgeTimeToFirstByteMsP95",),
        "Edge time to first byte, p95",
        rate=False,
    ),
    CloudflareSignal(
        "cf_ttfb_p99",
        _EDGE,
        Unit.SECONDS,
        Query.TIMING,
        ("quantiles.edgeTimeToFirstByteMsP99",),
        "Edge time to first byte, p99",
        rate=False,
    ),
    CloudflareSignal(
        "cf_origin_p95",
        _EDGE,
        Unit.SECONDS,
        Query.TIMING,
        ("quantiles.originResponseDurationMsP95",),
        "Origin response time, p95",
        rate=False,
    ),
    CloudflareSignal(
        "cf_blocked",
        _SEC,
        Unit.PER_SECOND,
        Query.SECURITY,
        ("count", "action"),
        "Requests blocked by WAF/firewall rules (block, connection close)",
    ),
    CloudflareSignal(
        "cf_challenged",
        _SEC,
        Unit.PER_SECOND,
        Query.SECURITY,
        ("count", "action"),
        "Challenges issued (interactive, JS and managed challenges)",
    ),
)
BY_SIGNAL = {s.signal: s for s in SIGNALS}

TRAFFIC_ALIASES: dict[str, tuple[str, str]] = {
    "requests": ("cf_requests", ""),
    "status5xx": ("cf_5xx", "edgeResponseStatus_geq: 500, edgeResponseStatus_lt: 600"),
    "origin52x": ("cf_52x", "edgeResponseStatus_geq: 520, edgeResponseStatus_lt: 531"),
    "status404": ("cf_404", "edgeResponseStatus: 404"),
    "status4xx": ("cf_4xx", "edgeResponseStatus_geq: 400, edgeResponseStatus_lt: 500"),
    "cacheHits": ("cf_cache_hits", 'cacheStatus_in: ["hit", "stale", "updating", "revalidated"]'),
}
"""Alias -> (signal, extra filter) of the traffic document."""

TIMING_FIELDS = {
    "edgeTimeToFirstByteMsP95": "cf_ttfb_p95",
    "edgeTimeToFirstByteMsP99": "cf_ttfb_p99",
    "originResponseDurationMsP95": "cf_origin_p95",
}

BLOCKED_ACTIONS = frozenset({"block", "connectionclose"})
CHALLENGED_ACTIONS = frozenset({"challenge", "jschallenge", "managedchallenge"})
"""Firewall actions, compared without case or underscores (``managed_challenge``)."""


def action_signal(action: str) -> str | None:
    normalised = action.lower().replace("_", "")
    if normalised in BLOCKED_ACTIONS:
        return "cf_blocked"
    if normalised in CHALLENGED_ACTIONS:
        return "cf_challenged"
    return None


def _hosts(hostnames: list[str]) -> str:
    if not hostnames:
        return ""
    return ", clientRequestHTTPHost_in: [" + ", ".join(f'"{h}"' for h in hostnames) + "]"


def _zone(zone_id: str, body: str) -> str:
    return (
        f'{{\n  viewer {{\n    zones(filter: {{zoneTag: "{zone_id}"}}) {{\n{body}    }}\n  }}\n}}'
    )


def _group(alias: str, dataset: str, filt: str, limit: int, fields: str) -> str:
    return (
        f"      {alias}: {dataset}(\n"
        f"        limit: {limit}\n"
        f"        filter: {{{filt}}}\n"
        f"        orderBy: [datetimeFiveMinutes_ASC]\n"
        f"      ) {{\n        {fields}\n      }}\n"
    )


def _range(start: str, end: str) -> str:
    return f'datetime_geq: "{start}", datetime_lt: "{end}"'


def traffic_document(zone_id: str, hostnames: list[str], start: str, end: str, limit: int) -> str:
    base = _range(start, end) + ', requestSource: "eyeball"' + _hosts(hostnames)
    body = ""
    for alias, (signal, extra) in TRAFFIC_ALIASES.items():
        fields = "count\n        dimensions { datetimeFiveMinutes }"
        if signal == "cf_requests":
            fields = (
                "count\n        avg { sampleInterval }\n        dimensions { datetimeFiveMinutes }"
            )
        filt = f"{base}, {extra}" if extra else base
        body += _group(alias, HTTP_DATASET, filt, limit, fields)
    return _zone(zone_id, body)


def timing_document(zone_id: str, hostnames: list[str], start: str, end: str, limit: int) -> str:
    base = _range(start, end) + ', requestSource: "eyeball"' + _hosts(hostnames)
    quantiles = " ".join(TIMING_FIELDS)
    fields = f"quantiles {{ {quantiles} }}\n        dimensions {{ datetimeFiveMinutes }}"
    return _zone(zone_id, _group("timing", HTTP_DATASET, base, limit, fields))


def security_document(zone_id: str, hostnames: list[str], start: str, end: str, limit: int) -> str:
    base = _range(start, end) + _hosts(hostnames)
    fields = "count\n        dimensions { datetimeFiveMinutes action }"
    return _zone(zone_id, _group("events", FIREWALL_DATASET, base, limit, fields))


def settings_document(zone_id: str) -> str:
    fields = "enabled maxDuration notOlderThan maxPageSize"
    body = "      settings {\n"
    body += "".join(
        f"        {dataset} {{ {fields} }}\n" for dataset in (HTTP_DATASET, FIREWALL_DATASET)
    )
    body += "      }\n"
    return _zone(zone_id, body)


def daily_document(zone_id: str, first: str, last: str) -> str:
    body = (
        "      httpRequests1dGroups(\n"
        "        limit: 40\n"
        f'        filter: {{date_geq: "{first}", date_leq: "{last}"}}\n'
        "        orderBy: [date_ASC]\n"
        "      ) {\n        sum { requests }\n        dimensions { date }\n      }\n"
    )
    return _zone(zone_id, body)


DOCUMENTS = {
    Query.TRAFFIC: traffic_document,
    Query.TIMING: timing_document,
    Query.SECURITY: security_document,
}


def display_query(query: Query, zone_id: str, hostnames: list[str], chunk_seconds: int) -> str:
    """The document of one signal with the chunk bounds as placeholders (evidence text)."""
    start, end = CHUNK_PLACEHOLDER
    doc = DOCUMENTS[query](zone_id, hostnames, start, end, 10000)
    return f"# Cloudflare GraphQL, one request per {chunk_seconds} s chunk [$start, $end)\n{doc}"
