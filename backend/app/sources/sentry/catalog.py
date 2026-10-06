"""Sentry signals and the ``events-timeseries`` requests that fetch them.

Each query is one request per time chunk with several ``yAxis`` aggregates at a 5-minute
interval, scoped to projects (by numeric ID) and the environment by request parameters. Tag
filters become quoted ``key:"value"`` search terms, so a value can never change the query.
Transactions come from the ``spans`` dataset where Sentry stores them there (sentry.io),
else from the classic ``transactions`` dataset (self-hosted).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, urlencode

from app.domain.common import SignalFamily, Unit

CATALOG_VERSION = "sentry-2026.10.7"
CHUNK_PLACEHOLDER = ("$start", "$end")
INTERVAL = "5m"
TIMESERIES_PATH = "/api/0/organizations/{organization}/events-timeseries/"
PROJECT_PATH = "/api/0/projects/{organization}/{project}/"


class Query(StrEnum):
    ERRORS = "errors"
    UNHANDLED = "unhandled"
    TRANSACTIONS = "transactions"


@dataclass(frozen=True)
class QuerySpec:
    dataset: str
    filter: str
    y_axes: tuple[str, ...]


SPANS_DATASET = "spans"
TRANSACTIONS_DATASET = "transactions"
TRANSACTION_DATASETS = (SPANS_DATASET, TRANSACTIONS_DATASET)
"""Tried in this order; the first one that is accepted and has transactions is used."""

_QUERIES = {
    Query.ERRORS: QuerySpec("errors", "", ("count()", "count_unique(user)")),
    Query.UNHANDLED: QuerySpec("errors", "error.unhandled:true", ("count()",)),
}
_TRANSACTION_QUERIES = {
    SPANS_DATASET: QuerySpec(
        SPANS_DATASET,
        "is_transaction:true",
        ("count()", "failure_rate()", "p95(span.duration)", "p99(span.duration)"),
    ),
    TRANSACTIONS_DATASET: QuerySpec(
        TRANSACTIONS_DATASET,
        "",
        ("count()", "failure_rate()", "p95(transaction.duration)", "p99(transaction.duration)"),
    ),
}


def query_spec(query: Query, transactions_dataset: str = SPANS_DATASET) -> QuerySpec:
    if query is Query.TRANSACTIONS:
        return _TRANSACTION_QUERIES[transactions_dataset]
    return _QUERIES[query]


@dataclass(frozen=True)
class SentrySignal:
    signal: str
    family: SignalFamily
    unit: Unit
    query: Query
    axis: int
    """Index of the signal's aggregate in the query's ``y_axes``."""
    description: str
    rate: bool = True
    """Counts per bucket, analysed per second."""
    zero_fill: bool = True
    """A bucket without a value inside a fetched chunk had no events; durations stay unknown."""

    def aggregate(self, transactions_dataset: str = SPANS_DATASET) -> str:
        return query_spec(self.query, transactions_dataset).y_axes[self.axis]


_ERR, _PERF = SignalFamily.APP_ERRORS, SignalFamily.APP_PERFORMANCE
SIGNALS = (
    SentrySignal("sentry_errors", _ERR, Unit.PER_SECOND, Query.ERRORS, 0, "Error events"),
    SentrySignal(
        "sentry_error_users",
        _ERR,
        Unit.COUNT,
        Query.ERRORS,
        1,
        "Users who hit an error, per 5 minutes",
        rate=False,
    ),
    SentrySignal(
        "sentry_unhandled",
        _ERR,
        Unit.PER_SECOND,
        Query.UNHANDLED,
        0,
        "Unhandled errors (crashes), error.unhandled:true",
    ),
    SentrySignal(
        "sentry_transactions",
        _PERF,
        Unit.REQUESTS_PER_SECOND,
        Query.TRANSACTIONS,
        0,
        "Transactions",
    ),
    SentrySignal(
        "sentry_transaction_failures",
        _PERF,
        Unit.REQUESTS_PER_SECOND,
        Query.TRANSACTIONS,
        1,
        "Failed transactions (failure_rate() * count())",
    ),
    SentrySignal(
        "sentry_duration_p95",
        _PERF,
        Unit.SECONDS,
        Query.TRANSACTIONS,
        2,
        "Transaction duration, p95",
        rate=False,
        zero_fill=False,
    ),
    SentrySignal(
        "sentry_duration_p99",
        _PERF,
        Unit.SECONDS,
        Query.TRANSACTIONS,
        3,
        "Transaction duration, p99",
        rate=False,
        zero_fill=False,
    ),
)
BY_SIGNAL = {s.signal: s for s in SIGNALS}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search(spec: QuerySpec, tags: Sequence[tuple[str, str]]) -> str:
    """The query's own filter AND every tag filter."""
    terms = [spec.filter] if spec.filter else []
    terms += [f"{key}:{_quote(value)}" for key, value in tags]
    return " ".join(terms)


def timeseries_params(
    spec: QuerySpec,
    project_ids: Sequence[str],
    environment: str | None,
    tags: Sequence[tuple[str, str]],
    start: str,
    end: str,
) -> list[tuple[str, str]]:
    params = [("dataset", spec.dataset)] + [("project", p) for p in project_ids]
    if environment:
        params.append(("environment", environment))
    if query := search(spec, tags):
        params.append(("query", query))
    params += [("yAxis", axis) for axis in spec.y_axes]
    params += [("interval", INTERVAL), ("start", start), ("end", end)]
    return params


def timeseries_path(organization: str) -> str:
    return TIMESERIES_PATH.format(organization=quote(organization, safe=""))


def project_path(organization: str, project: str) -> str:
    return PROJECT_PATH.format(
        organization=quote(organization, safe=""), project=quote(project, safe="")
    )


def request_text(
    spec: QuerySpec,
    organization: str,
    project_ids: Sequence[str],
    environment: str | None,
    tags: Sequence[tuple[str, str]],
    start: str,
    end: str,
) -> str:
    """The request line of one chunk, as evidence (the host is in the report's source info)."""
    params = timeseries_params(spec, project_ids, environment, tags, start, end)
    return f"GET {timeseries_path(organization)}?{urlencode(params, safe='()$:,')}"


def display_query(
    spec: QuerySpec,
    organization: str,
    project_id: str,
    environment: str | None,
    tags: Sequence[tuple[str, str]],
    chunk_seconds: int,
) -> str:
    """The request of one signal with the chunk bounds as placeholders (evidence text)."""
    start, end = CHUNK_PLACEHOLDER
    line = request_text(spec, organization, [project_id], environment, tags, start, end)
    header = f"# Sentry events-timeseries, one request per {chunk_seconds} s chunk [$start, $end)"
    return f"{header}\n{line}"
