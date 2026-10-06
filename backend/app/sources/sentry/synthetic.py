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
from app.sources.sentry.api import SECONDS_PER_DAY, Chunk, ProjectInfo
from app.sources.sentry.catalog import BY_SIGNAL, SPANS_DATASET, Query, query_spec, request_text

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
    """A release of the first project crashes for 90 min ending 4 h before T: errors,
    unhandled errors, affected users, failed and slow transactions climb."""
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
            self._data = {
                str(i + 1): self._generate(i, incidents=i == 0) for i in range(len(self.slugs))
            }

    async def aclose(self) -> None:
        pass

    # --- generation -----------------------------------------------------------------------

    def _generate(self, index: int, *, incidents: bool) -> dict[str, np.ndarray]:
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

        if incidents and sc.bad_deploy:
            w = _window(4, 1.5)
            errors[w] *= 18
            unhandled[w] = 0.25
            users[w] *= 10
            failure[w] = 0.09
            p95[w] *= 2.5
        if incidents and sc.slowdown:
            p95[_window(7, 2)] *= 3
        if incidents and sc.traffic_drop:
            tps[_window(9, 3)] *= 0.3

        transactions = np.round(tps * STEP) if sc.tracing else np.zeros(N)
        return {
            "sentry_errors": np.round(errors * STEP),
            "sentry_error_users": np.round(users),
            "sentry_unhandled": np.round(unhandled * STEP),
            "sentry_transactions": transactions,
            "sentry_transaction_failures": np.round(transactions * np.clip(failure, 0, 1)),
            "sentry_duration_p95": p95,
            "sentry_duration_p99": p95 * 2.2,
        }

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

    async def series(self, query: Query, project_ids: Sequence[str], start: int, end: int) -> Chunk:
        grid_start = self._grid_start()
        failing = self.scenario.failing_day
        if failing is not None and query is not Query.TRANSACTIONS:
            day_start = grid_start + N * STEP - failing * SECONDS_PER_DAY
            if start < day_start + SECONDS_PER_DAY and end > day_start:
                raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 503 (synthetic)")
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
        lo = max((start - grid_start) // STEP, self._first_index(), 0)
        hi = min((end - grid_start) // STEP, N)
        for i in range(lo, max(hi, lo)):
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
