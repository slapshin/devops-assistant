"""Deterministic synthetic Sentry API for tests, UI development and offline demos.

It answers the same calls as the REST client, so a ``synthetic://<scenario>`` Sentry source
runs the real collection and conversion code. Scenario names match the Prometheus synthetic
source; anomalies are placed relative to the analysis end time T.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import SentryTag
from app.sources.base import HISTORY_DAYS
from app.sources.sentry.api import SECONDS_PER_DAY, Chunk, IssueInfo, ProjectInfo
from app.sources.sentry.catalog import (
    BY_SIGNAL,
    SPANS_DATASET,
    Query,
    issue_spec,
    query_spec,
    request_text,
)

STEP = 300
DAY = SECONDS_PER_DAY // STEP
N = HISTORY_DAYS * DAY
HOUR = DAY // 24


@dataclass(frozen=True)
class SentryScenario:
    history_days: int = HISTORY_DAYS
    tracing: bool = True
    """False: the project sends no transactions (tracing is not set up)."""
    bad_deploy: bool = False
    """A release of the first project crashes for 90 min ending 4 h before T: a new error
    kind appears, unhandled errors, affected users, failed and slow transactions climb."""
    slowdown: bool = False
    """Transactions are three times slower for 2 h ending 7 h before T."""
    traffic_drop: bool = False
    """Transactions fall to 30 % for 3 h ending 9 h before T."""
    failing_day: int | None = None
    """Error queries for this many days before T fail (a partial report)."""
    seed: int = 23


SCENARIOS: dict[str, SentryScenario] = {
    "healthy": SentryScenario(),
    "incident": SentryScenario(bad_deploy=True),
    "short-history": SentryScenario(history_days=5, tracing=False),
    "degraded": SentryScenario(slowdown=True, traffic_drop=True, failing_day=10),
}

SYNTHETIC_ORGANIZATION = "demo"

ERROR_KINDS = (
    ("TimeoutError: upstream request timed out after 30s", 0.45),
    ("KeyError: 'currency'", 0.25),
    ("ValidationError: invalid email address", 0.15),
)
"""(title, share of the baseline error events); the rest is a long tail of rare errors."""
DEPLOY_ERROR = "TypeError: 'NoneType' object is not subscriptable"
"""The error kind the bad deploy introduces."""
TAIL_SHARE = 1 - sum(share for _, share in ERROR_KINDS)


def _window(end_hours_before: int, hours: float) -> slice:
    stop = N - end_hours_before * HOUR
    return slice(stop - int(hours * HOUR), stop)


class SyntheticSentryApi:
    """Projects get IDs 1, 2, ... in slug order; anomalies happen in the first project only."""

    def __init__(
        self,
        scenario: str,
        organization: str,
        projects: Sequence[str],
        environment: str | None,
        tags: Sequence[SentryTag] = (),
    ) -> None:
        self.name = scenario
        self.scenario = SCENARIOS[scenario]
        self.organization = organization
        self.slugs = sorted(projects)
        self.environment = environment
        self.tags = [(t.key, t.value) for t in tags]
        self.transactions_dataset = SPANS_DATASET
        self._anchor: int | None = None
        self._data: dict[str, dict[str, np.ndarray]] = {}
        self._issues: dict[str, dict[str, tuple[str, np.ndarray]]] = {}
        """Project ID -> short issue ID -> (title, error events per bucket)."""

    @property
    def base_url(self) -> str:
        return f"synthetic://{self.name}"

    @property
    def backend(self) -> str | None:
        return "synthetic"

    def begin(self, end_time: datetime) -> None:
        anchor = int(end_time.timestamp())
        if anchor != self._anchor:
            self._anchor = anchor
            self._data, self._issues = {}, {}
            for i in range(len(self.slugs)):
                pid = str(i + 1)
                self._data[pid], self._issues[pid] = self._generate(i, incidents=i == 0)

    async def aclose(self) -> None:
        pass

    # --- generation -----------------------------------------------------------------------

    def _generate(
        self, index: int, *, incidents: bool
    ) -> tuple[dict[str, np.ndarray], dict[str, tuple[str, np.ndarray]]]:
        sc = self.scenario
        rng = np.random.default_rng(sc.seed + index)
        tod = (np.arange(N) % DAY) / DAY
        diurnal = np.sin(2 * np.pi * (tod - 0.35))
        scale = 1.0 / (1 + index)

        def noisy(level: float, amp: float, noise: float) -> np.ndarray:
            return np.clip(level + amp * diurnal + rng.normal(0, noise, N), 0, None)

        tps = noisy(12.0, 7.0, 0.6) * scale
        failure = noisy(0.004, 0.0, 0.0008)
        p95 = noisy(0.32, 0.04, 0.015)
        errors = noisy(0.05, 0.02, 0.006)
        unhandled = noisy(0.004, 0.0, 0.0015)
        users = noisy(4.0, 2.0, 0.6)

        prefix = self.slugs[index].upper()
        issues = {
            f"{prefix}-{n + 1}": (title, np.round(errors * share * STEP))
            for n, (title, share) in enumerate(ERROR_KINDS)
        }
        tail = np.round(errors * TAIL_SHARE * STEP)
        if incidents and sc.bad_deploy:
            w = _window(4, 1.5)
            deploy = np.zeros(N)
            deploy[w] = np.round(errors[w] * 17 * STEP)
            issues[f"{prefix}-{len(ERROR_KINDS) + 1}"] = (DEPLOY_ERROR, deploy)
            unhandled[w] = 0.25
            users[w] *= 10
            failure[w] = 0.09
            p95[w] *= 2.5
        if incidents and sc.slowdown:
            p95[_window(7, 2)] *= 3
        if incidents and sc.traffic_drop:
            tps[_window(9, 3)] *= 0.3

        transactions = np.round(tps * STEP) if sc.tracing else np.zeros(N)
        data = {
            "sentry_errors": tail + sum(counts for _, counts in issues.values()),
            "sentry_error_users": np.round(users),
            "sentry_unhandled": np.round(unhandled * STEP),
            "sentry_transactions": transactions,
            "sentry_transaction_failures": np.round(transactions * np.clip(failure, 0, 1)),
            "sentry_duration_p95": p95,
            "sentry_duration_p99": p95 * 2.2,
        }
        return data, issues

    def _first_index(self) -> int:
        return N - self.scenario.history_days * DAY

    def _grid_start(self) -> int:
        if self._anchor is None:
            raise RuntimeError("begin() must be called before querying synthetic data")
        return self._anchor - N * STEP

    def _iso(self, ts: int) -> str:
        return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- SentryApi ------------------------------------------------------------------------

    async def projects(self) -> list[ProjectInfo]:
        created = datetime.fromtimestamp(self._grid_start() + self._first_index() * STEP, UTC)
        return [
            ProjectInfo(id=str(i + 1), slug=slug, name=slug, created=created)
            for i, slug in enumerate(self.slugs)
        ]

    def _fail_errors(self, start: int, end: int) -> None:
        failing = self.scenario.failing_day
        if failing is not None:
            day_start = self._grid_start() + N * STEP - failing * SECONDS_PER_DAY
            if start < day_start + SECONDS_PER_DAY and end > day_start:
                raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 503 (synthetic)")

    def _range(self, start: int, end: int) -> range:
        grid_start = self._grid_start()
        lo = max((start - grid_start) // STEP, self._first_index(), 0)
        hi = min((end - grid_start) // STEP, N)
        return range(lo, max(hi, lo))

    async def top_issues(
        self, project_ids: Sequence[str], start: int, end: int, limit: int
    ) -> list[IssueInfo]:
        span = self._range(start, end)
        found = [
            IssueInfo(issue, title, float(counts[span.start : span.stop].sum()))
            for pid in project_ids
            for issue, (title, counts) in self._issues[pid].items()
        ]
        found = sorted((i for i in found if i.count), key=lambda i: (-i.count, i.issue))
        return found[:limit]

    async def issue_series(
        self, issues: Sequence[str], project_ids: Sequence[str], start: int, end: int
    ) -> Chunk:
        self._fail_errors(start, end)
        spec = issue_spec(issues)
        text = request_text(
            spec,
            self.organization,
            project_ids,
            self.environment,
            self.tags,
            self._iso(start),
            self._iso(end),
            top=len(issues),
        )
        chunk = Chunk(query=text)
        grid_start = self._grid_start()
        for pid in project_ids:
            for issue, (_, counts) in self._issues[pid].items():
                if issue in issues:
                    chunk.groups[issue] = {
                        grid_start + i * STEP: float(counts[i]) for i in self._range(start, end)
                    }
        return chunk

    async def series(self, query: Query, project_ids: Sequence[str], start: int, end: int) -> Chunk:
        grid_start = self._grid_start()
        if query is not Query.TRANSACTIONS:
            self._fail_errors(start, end)
        text = request_text(
            query_spec(query, self.transactions_dataset),
            self.organization,
            project_ids,
            self.environment,
            self.tags,
            self._iso(start),
            self._iso(end),
        )
        data = [self._data[pid] for pid in project_ids]
        signals = [s for s in BY_SIGNAL.values() if s.query is query]
        chunk = Chunk(query=text, values={s.signal: {} for s in signals})
        for i in self._range(start, end):
            bucket = grid_start + i * STEP
            traced = sum(float(d["sentry_transactions"][i]) for d in data)
            for defn in signals:
                if defn.zero_fill:
                    value = sum(float(d[defn.signal][i]) for d in data)
                elif not traced:
                    continue  # no duration without transactions
                else:  # transaction-weighted mean of the projects' quantiles (an estimate)
                    weighted = (
                        float(d[defn.signal][i] * d["sentry_transactions"][i]) for d in data
                    )
                    value = sum(weighted) / traced
                chunk.values[defn.signal][bucket] = value
        return chunk
