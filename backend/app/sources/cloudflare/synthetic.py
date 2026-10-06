"""Deterministic synthetic Cloudflare API for tests, UI development and offline demos.

It answers the same calls as the GraphQL client, so a ``synthetic://<scenario>`` Cloudflare
source runs the real collection and conversion code. Scenario names match the Prometheus
synthetic source; anomalies are placed relative to the analysis end time T.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np

from app.domain.interfaces import SourceError, SourceErrorKind
from app.metrics.source import HISTORY_DAYS
from app.sources.cloudflare.api import (
    DATASETS,
    SECONDS_PER_DAY,
    Chunk,
    DatasetSettings,
)
from app.sources.cloudflare.catalog import (
    security_document,
    timing_document,
    traffic_document,
)

STEP = 300
DAY = SECONDS_PER_DAY // STEP
N = HISTORY_DAYS * DAY
LATEST = N - DAY
HOUR = DAY // 24


@dataclass(frozen=True)
class CloudflareScenario:
    history_days: int = HISTORY_DAYS
    timing: bool = True
    """False: the zone's plan has no timing quantiles (the probe is refused)."""
    origin_outage: bool = False
    """The origin times out for 45 min ending 3 h before T: 52x errors, TTFB and 5xx climb."""
    attack: bool = False
    """A bot wave for 2 h ending 6 h before T: blocked and challenged requests surge."""
    cache_drop: bool = False
    """The cache hit share halves for 3 h ending 5 h before T (e.g. after a purge)."""
    traffic_drop: bool = False
    """Requests fall to 30 % for 3 h ending 8 h before T."""
    failing_day: int | None = None
    """Traffic queries for this many days before T fail (a partial report)."""
    seed: int = 11


SCENARIOS: dict[str, CloudflareScenario] = {
    "healthy": CloudflareScenario(),
    "incident": CloudflareScenario(origin_outage=True, attack=True),
    "short-history": CloudflareScenario(history_days=5, timing=False),
    "degraded": CloudflareScenario(cache_drop=True, traffic_drop=True, failing_day=3),
}

SYNTHETIC_ZONE_ID = "0" * 32


def _window(end_hours_before: int, hours: float) -> slice:
    stop = N - end_hours_before * HOUR
    return slice(stop - int(hours * HOUR), stop)


class SyntheticCloudflareApi:
    def __init__(self, scenario: str, zone_id: str, hostnames: list[str]) -> None:
        self.name = scenario
        self.scenario = SCENARIOS[scenario]
        self.zone_id = zone_id
        self.hostnames = hostnames
        self._anchor: int | None = None
        self._data: dict[str, np.ndarray] = {}

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
            self._data = self._generate()

    async def aclose(self) -> None:
        pass

    # --- generation -----------------------------------------------------------------------

    def _generate(self) -> dict[str, np.ndarray]:
        sc = self.scenario
        rng = np.random.default_rng(sc.seed)
        tod = (np.arange(N) % DAY) / DAY
        diurnal = np.sin(2 * np.pi * (tod - 0.35))

        def noisy(level: float, amp: float, noise: float) -> np.ndarray:
            return np.clip(level + amp * diurnal + rng.normal(0, noise, N), 0, None)

        rps = noisy(40.0, 25.0, 2.0)
        share_5xx = noisy(0.002, 0.0, 0.0003)
        share_52x = noisy(0.0005, 0.0, 0.0001)
        share_404 = noisy(0.01, 0.0, 0.001)
        share_4xx = share_404 + noisy(0.02, 0.0, 0.002)
        share_cache = noisy(0.55, 0.05, 0.02)
        ttfb_p95 = noisy(0.18, 0.03, 0.01)
        ttfb_p99 = ttfb_p95 * 2.5
        origin_p95 = noisy(0.35, 0.05, 0.02)
        blocked = noisy(0.08, 0.03, 0.01)
        challenged = noisy(0.15, 0.05, 0.02)

        if sc.origin_outage:
            w = _window(3, 0.75)
            share_52x[w] = 0.12
            share_5xx[w] = 0.13
            ttfb_p95[w] *= 4
            ttfb_p99[w] *= 5
            origin_p95[w] = 8.0
        if sc.attack:
            w = _window(6, 2)
            blocked[w] *= 30
            challenged[w] *= 12
        if sc.cache_drop:
            share_cache[_window(5, 3)] *= 0.45
        if sc.traffic_drop:
            rps[_window(8, 3)] *= 0.3

        requests = np.round(rps * STEP)
        return {
            "cf_requests": requests,
            "cf_5xx": np.round(requests * share_5xx),
            "cf_52x": np.round(requests * share_52x),
            "cf_404": np.round(requests * share_404),
            "cf_4xx": np.round(requests * share_4xx),
            "cf_cache_hits": np.round(requests * np.clip(share_cache, 0, 1)),
            "cf_ttfb_p95": ttfb_p95,
            "cf_ttfb_p99": ttfb_p99,
            "cf_origin_p95": origin_p95,
            "cf_blocked": np.round(blocked * STEP),
            "cf_challenged": np.round(challenged * STEP),
        }

    def _first_index(self) -> int:
        return N - self.scenario.history_days * DAY

    def _indices(self, start: int, end: int) -> range:
        if self._anchor is None:
            raise RuntimeError("begin() must be called before querying synthetic data")
        grid_start = self._anchor - N * STEP
        lo = max((start - grid_start) // STEP, self._first_index())
        hi = min((end - grid_start) // STEP, N)
        return range(max(lo, 0), max(hi, 0))

    def _chunk(self, query: str, signals: tuple[str, ...], start: int, end: int) -> Chunk:
        assert self._anchor is not None
        grid_start = self._anchor - N * STEP
        chunk = Chunk(query=query, values={s: {} for s in signals})
        for i in self._indices(start, end):
            bucket = grid_start + i * STEP
            for signal in signals:
                value = float(self._data[signal][i])
                if value:
                    chunk.values[signal][bucket] = value
            if "cf_requests" in signals:
                chunk.sample_intervals[bucket] = 1.0
        return chunk

    def _iso(self, ts: int) -> str:
        return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- CloudflareApi --------------------------------------------------------------------

    async def settings(self) -> dict[str, DatasetSettings]:
        not_older_than = self.scenario.history_days * SECONDS_PER_DAY
        return {d: DatasetSettings(not_older_than=not_older_than) for d in DATASETS}

    async def traffic(self, start: int, end: int, page_size: int) -> Chunk:
        failing = self.scenario.failing_day
        if failing is not None and self._anchor is not None:
            day_start = self._anchor - failing * SECONDS_PER_DAY
            if start < day_start + SECONDS_PER_DAY and end > day_start:
                raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 503 (synthetic)")
        query = traffic_document(
            self.zone_id, self.hostnames, self._iso(start), self._iso(end), page_size
        )
        signals = ("cf_requests", "cf_5xx", "cf_52x", "cf_404", "cf_4xx", "cf_cache_hits")
        return self._chunk(query, signals, start, end)

    async def timing(self, start: int, end: int, page_size: int) -> Chunk:
        if not self.scenario.timing:
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                "zone plan does not include quantiles of httpRequestsAdaptiveGroups (synthetic)",
            )
        query = timing_document(
            self.zone_id, self.hostnames, self._iso(start), self._iso(end), page_size
        )
        return self._chunk(query, ("cf_ttfb_p95", "cf_ttfb_p99", "cf_origin_p95"), start, end)

    async def security(self, start: int, end: int, page_size: int) -> Chunk:
        query = security_document(
            self.zone_id, self.hostnames, self._iso(start), self._iso(end), page_size
        )
        return self._chunk(query, ("cf_blocked", "cf_challenged"), start, end)

    async def daily_requests(self, first: date, last: date) -> dict[date, float]:
        assert self._anchor is not None
        grid_start = self._anchor - N * STEP
        out: dict[date, float] = {}
        for i in range(self._first_index(), N):
            day = datetime.fromtimestamp(grid_start + i * STEP, UTC).date()
            if first <= day <= last:
                out[day] = out.get(day, 0.0) + float(self._data["cf_requests"][i])
        return out
