"""Wazuh signals and the indexer searches that fetch them.

Alerts live in the Wazuh indexer (an OpenSearch fork), one index per day matching
``wazuh-alerts-4.x-*``. Every request is a ``size: 0`` aggregation search whose query is JSON
DSL made of ``term``/``terms``/``range`` clauses only, so a configured agent name, label or
value can never change the query (there is no query-string parsing).

One collection request covers all agents of a time chunk: ``terms`` on ``agent.name``, a
5-minute ``date_histogram`` per agent, and a ``filters`` sub-aggregation with one bucket per
filtered signal (the histogram bucket's own count is the "all alerts" signal).

Alerts do not carry an agent's groups. Groups are resolved to agent names from the
``wazuh-monitoring-*`` index, where the Wazuh dashboard stores a snapshot of every agent (name,
status, groups) every 15 minutes; those names then select alerts like configured ones.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.domain.common import SignalFamily, Unit

CATALOG_VERSION = "wazuh-2026.10.8"
TIMESTAMP_FIELD = "timestamp"
AGENT_FIELD = "agent.name"
LABELS_FIELD = "agent.labels"
INTERVAL = "5m"
MAX_AGENTS = 50
"""Agents analysed per source; more are reported as truncated, never silently cut."""
BUCKET_BUDGET = 40_000
"""Aggregation buckets per request, well under OpenSearch's default search.max_buckets (65535)."""
MAX_CHUNK_SECONDS = 7 * 86400
CHUNK_PLACEHOLDER = ("$start", "$end")
HIGH_ALERT_LEVEL = 12
"""Wazuh's "high" band starts at rule level 12 (12-14 high, 15 critical)."""
AUTH_FAILURE_GROUPS = ("authentication_failed", "authentication_failures", "invalid_login")
FIM_GROUP = "syscheck"
MONITORING_NAME_FIELD = "name"
MONITORING_GROUP_FIELD = "group"
MONITORING_TIMESTAMP_FIELD = "timestamp"
MEMBERSHIP_SECONDS = 86400
"""Group members are the agents in a group in any monitoring snapshot of the last 24 h."""
MAX_GROUP_MEMBERS = 1000
"""Agents read per group; more are reported as truncated."""


@dataclass(frozen=True)
class WazuhSignal:
    signal: str
    family: SignalFamily
    description: str
    filter: dict[str, Any] | None
    """Extra filter for this signal; None counts every alert."""
    unit: Unit = Unit.PER_SECOND


_HOST, _FIM = SignalFamily.HOST_SECURITY, SignalFamily.FILE_INTEGRITY
SIGNALS = (
    WazuhSignal("wazuh_alerts", _HOST, "All alerts", None),
    WazuhSignal(
        "wazuh_high_alerts",
        _HOST,
        f"Alerts with rule.level >= {HIGH_ALERT_LEVEL}",
        {"range": {"rule.level": {"gte": HIGH_ALERT_LEVEL}}},
    ),
    WazuhSignal(
        "wazuh_auth_failures",
        _HOST,
        "Authentication failures (rule.groups: " + ", ".join(AUTH_FAILURE_GROUPS) + ")",
        {"terms": {"rule.groups": list(AUTH_FAILURE_GROUPS)}},
    ),
    WazuhSignal(
        "wazuh_fim_changes",
        _FIM,
        f"File integrity changes (rule.groups: {FIM_GROUP})",
        {"term": {"rule.groups": FIM_GROUP}},
    ),
)
BY_SIGNAL = {s.signal: s for s in SIGNALS}
FILTERED = tuple(s for s in SIGNALS if s.filter is not None)


def search_path(index_pattern: str) -> str:
    return f"/{quote(index_pattern, safe='*')}/_search"


def time_range(start: int | str, end: int | str) -> dict[str, Any]:
    """Half-open [start, end) in epoch seconds."""
    return {"range": {TIMESTAMP_FIELD: {"gte": start, "lt": end, "format": "epoch_second"}}}


def scope_filters(agents: Sequence[str], labels: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """The source's agent selection: listed names AND every label filter."""
    filters: list[dict[str, Any]] = []
    if agents:
        filters.append({"terms": {AGENT_FIELD: list(agents)}})
    filters += [{"term": {f"{LABELS_FIELD}.{key}": value}} for key, value in labels]
    return filters


def members_body(groups: Sequence[str], start: int, end: int) -> dict[str, Any]:
    """Agent names per configured group in monitoring snapshots of [start, end)."""
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            MONITORING_TIMESTAMP_FIELD: {
                                "gte": start,
                                "lt": end,
                                "format": "epoch_second",
                            }
                        }
                    },
                    {"terms": {MONITORING_GROUP_FIELD: list(groups)}},
                ]
            }
        },
        "aggs": {
            "groups": {
                "terms": {
                    "field": MONITORING_GROUP_FIELD,
                    "include": list(groups),
                    "size": max(1, len(groups)),
                },
                "aggs": {
                    "agents": {
                        "terms": {
                            "field": MONITORING_NAME_FIELD,
                            "size": MAX_GROUP_MEMBERS + 1,
                            "order": {"_key": "asc"},
                        }
                    }
                },
            }
        },
    }


def discovery_body(
    agents: Sequence[str], labels: Sequence[tuple[str, str]], start: int, end: int
) -> dict[str, Any]:
    """Agents with alerts in [start, end), with their first alert, up to MAX_AGENTS + 1."""
    return {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": [time_range(start, end), *scope_filters(agents, labels)]}},
        "aggs": {
            "agents": {
                "terms": {"field": AGENT_FIELD, "size": MAX_AGENTS + 1, "order": {"_key": "asc"}},
                "aggs": {"first": {"min": {"field": TIMESTAMP_FIELD}}},
            }
        },
    }


def series_body(
    names: Sequence[str],
    labels: Sequence[tuple[str, str]],
    start: int,
    end: int,
) -> dict[str, Any]:
    """Per agent, 5-minute alert counts of every signal in [start, end).

    ``names`` are the discovered agents (already inside the configured selection), and the
    label filters are repeated so the request alone states the full scope.
    """
    return {
        "size": 0,
        "query": {"bool": {"filter": [time_range(start, end), *scope_filters(names, labels)]}},
        "aggs": {
            "agents": {
                "terms": {"field": AGENT_FIELD, "size": max(1, len(names))},
                "aggs": {
                    "time": {
                        "date_histogram": {"field": TIMESTAMP_FIELD, "fixed_interval": INTERVAL},
                        "aggs": {
                            "signals": {
                                "filters": {"filters": {s.signal: s.filter for s in FILTERED}}
                            }
                        },
                    }
                },
            }
        },
    }


def chunk_seconds(agents: int, step: int) -> int:
    """Widest chunk whose aggregation stays within BUCKET_BUDGET for this many agents."""
    per_bucket = max(1, agents) * (1 + len(FILTERED))
    buckets = max(1, BUCKET_BUDGET // per_bucket)
    hour = 3600
    seconds = min(buckets * step, MAX_CHUNK_SECONDS)
    return seconds // hour * hour if seconds >= hour else max(step, seconds // step * step)


def display_query(
    index_pattern: str,
    signal: WazuhSignal,
    agent: str,
    labels: Sequence[tuple[str, str]],
    chunk: int,
) -> str:
    """An equivalent single-agent search for one signal (evidence text).

    The real requests cover all agents of a chunk at once; the chunk bounds are placeholders.
    """
    start, end = CHUNK_PLACEHOLDER
    filters = [time_range(start, end), *scope_filters([agent], labels)]
    if signal.filter is not None:
        filters.append(signal.filter)
    body = {
        "size": 0,
        "query": {"bool": {"filter": filters}},
        "aggs": {
            "time": {"date_histogram": {"field": TIMESTAMP_FIELD, "fixed_interval": INTERVAL}}
        },
    }
    header = f"# Wazuh indexer search, one request per {chunk} s chunk [$start, $end)"
    return f"{header}\nPOST {search_path(index_pattern)}\n{json.dumps(body, sort_keys=True)}"


def request_text(path: str, body: dict[str, Any]) -> str:
    return f"POST {path} {json.dumps(body, sort_keys=True)}"
