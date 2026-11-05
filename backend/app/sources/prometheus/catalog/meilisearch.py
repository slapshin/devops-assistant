"""Meilisearch signals (the built-in `/metrics` endpoint, `--experimental-enable-metrics`)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    Direction,
    RateQuery,
    Role,
    SignalDef,
    aggregate,
    latency_quantiles,
    q,
    sel,
    sum_rate,
)

# One Meilisearch server is one scrape target (`job`, `instance`); its entities are the server,
# one index on it (plus `index`) and one API (plus `api`). Request metrics are labelled by route
# template (`path`, e.g. `/indexes/{index_uid}/search`), not by API, so `label_replace` names the
# API after the per-server aggregation; monitoring routes (`/health`, `/metrics`) are no API.
MEILISEARCH_SERVER = ("job", "instance")
MEILISEARCH_API = ("job", "instance", "api")
MEILISEARCH_INDEX = ("job", "instance", "index")

_REQUESTS = "meilisearch_http_requests_total"
_BUCKET = "meilisearch_http_response_time_seconds_bucket"
_DEGRADED = "meilisearch_degraded_search_requests"
_TASKS = "meilisearch_nb_tasks"
_QUEUE_LATENCY = "meilisearch_task_queue_latency_seconds"
_TASK_DB_USED = "meilisearch_task_queue_used_size"
_TASK_DB_LEFT = "meilisearch_task_queue_size_until_stop_registering"
_SEARCHES_WAITING = "meilisearch_searches_waiting_to_be_processed"
_SEARCH_QUEUE_SIZE = "meilisearch_search_queue_size"
_DOCS = "meilisearch_index_docs_count"

# API label value -> route templates of its requests.
_APIS = {
    "search": 'path=~"/multi-search|/indexes/[^/]+/(search|facet-search|similar)"',
    "documents": 'path=~"/indexes/[^/]+/documents(/.*)?"',
}
SEARCH_API = "search"


def traffic_signal(api: str) -> str:
    return f"meilisearch_{api}_requests"


def _api_rate(api: str, *api_matchers: str) -> RateQuery:
    """Per-API rate sum: aggregate without the (absent) `api` label, then name the API."""

    def by_entity(metric: str, by: tuple[str, ...], *matchers: str) -> str:
        server = tuple(k for k in by if k != "api")
        inner = sum_rate(metric, server, *api_matchers, *matchers)
        return f'label_replace({inner}, "api", "{api}", "", "")'

    return by_entity


def _api_signals(api: str, paths: str) -> tuple[SignalDef, ...]:
    """Request rate, 5xx/4xx/404 operands and p95/p99 of one API."""
    traffic = traffic_signal(api)
    by_entity = _api_rate(api, paths)

    def status(signal: str, family: SignalFamily, matcher: str, role: Role) -> SignalDef:
        return SignalDef(
            f"meilisearch_{api}_{signal}",
            family,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.DATABASE,
            MEILISEARCH_API,
            q(by_entity(_REQUESTS, MEILISEARCH_API, matcher), _REQUESTS),
            role=role,
            traffic=traffic,
        )

    return (
        SignalDef(
            traffic,
            SignalFamily.REQUEST_TRAFFIC,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.DATABASE,
            MEILISEARCH_API,
            q(by_entity(_REQUESTS, MEILISEARCH_API), _REQUESTS),
            direction=Direction.BOTH,
            traffic=traffic,
        ),
        status("5xx", SignalFamily.REQUEST_FAILURES, 'status=~"5.."', Role.OPERAND),
        status("4xx", SignalFamily.CLIENT_ERRORS, 'status=~"4.."', Role.SIGNAL),
        status("404", SignalFamily.CLIENT_ERRORS, 'status="404"', Role.SIGNAL),
        *latency_quantiles(
            f"meilisearch_{api}",
            EntityKind.DATABASE,
            MEILISEARCH_API,
            _BUCKET,
            traffic,
            by_entity=by_entity,
        ),
    )


def _server(agg: str, expr: str) -> str:
    return aggregate(agg, MEILISEARCH_SERVER, expr)


def _tasks(status: str) -> str:
    return _server("max", sel(_TASKS, 'kind="statuses"', f'value="{status}"'))


_DEGRADED_RATE = _api_rate(SEARCH_API)(_DEGRADED, MEILISEARCH_API)

MEILISEARCH_CATALOG: tuple[SignalDef, ...] = (
    *(signal for api, paths in _APIS.items() for signal in _api_signals(api, paths)),
    SignalDef(
        "meilisearch_degraded_searches",
        SignalFamily.SEARCH,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.DATABASE,
        MEILISEARCH_API,
        q(_DEGRADED_RATE, _DEGRADED),
        role=Role.OPERAND,
        traffic=traffic_signal(SEARCH_API),
        description="Searches cut short by searchCutoffMs; they returned partial results.",
    ),
    SignalDef(
        "meilisearch_task_queue_latency",
        SignalFamily.SEARCH,
        Unit.SECONDS,
        EntityKind.DATABASE,
        MEILISEARCH_SERVER,
        q(_server("max", f"max_over_time({sel(_QUEUE_LATENCY)}[{{w}}])"), _QUEUE_LATENCY),
        description="Age of the oldest enqueued or processing task (peak within the step).",
    ),
    SignalDef(
        "meilisearch_enqueued_tasks",
        SignalFamily.SEARCH,
        Unit.COUNT,
        EntityKind.DATABASE,
        MEILISEARCH_SERVER,
        q(_tasks("enqueued"), _TASKS),
        description="Tasks waiting to be processed (document writes, settings, deletions).",
    ),
    SignalDef(
        "meilisearch_failed_tasks",
        SignalFamily.SEARCH,
        Unit.COUNT,
        EntityKind.DATABASE,
        MEILISEARCH_SERVER,
        q(_tasks("failed"), _TASKS),
        description="Failed tasks kept in the task history (cumulative; analysed per step).",
    ),
    SignalDef(
        "meilisearch_task_db_used_ratio",
        SignalFamily.SEARCH,
        Unit.RATIO,
        EntityKind.DATABASE,
        MEILISEARCH_SERVER,
        q(
            f"{_server('max', sel(_TASK_DB_USED))} / (({_server('max', sel(_TASK_DB_USED))}"
            f" + {_server('max', sel(_TASK_DB_LEFT))}) > 0)",
            _TASK_DB_USED,
            _TASK_DB_LEFT,
        ),
        max_value=1.0,
        description=(
            "Task database used vs the size at which Meilisearch stops registering new tasks."
        ),
    ),
    SignalDef(
        "meilisearch_search_queue_used_ratio",
        SignalFamily.SEARCH,
        Unit.RATIO,
        EntityKind.DATABASE,
        MEILISEARCH_SERVER,
        q(
            f"{_server('max', f'max_over_time({sel(_SEARCHES_WAITING)}[{{w}}])')}"
            f" / ({_server('max', sel(_SEARCH_QUEUE_SIZE))} > 0)",
            _SEARCHES_WAITING,
            _SEARCH_QUEUE_SIZE,
        ),
        max_value=1.0,
        description="Searches waiting for a search thread vs the search queue size (peak).",
    ),
    SignalDef(
        "meilisearch_index_documents",
        SignalFamily.SEARCH,
        Unit.COUNT,
        EntityKind.DATABASE,
        MEILISEARCH_INDEX,
        q(aggregate("max", MEILISEARCH_INDEX, sel(_DOCS)), _DOCS),
        direction=Direction.DOWN,
        description="Documents in an index.",
    ),
)
