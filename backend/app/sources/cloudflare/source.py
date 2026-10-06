"""CloudflareMetricsSource: one zone's edge traffic and security events as metric series (T015).

The analysis window is fetched in chunks no wider than the dataset allows (and at most a day),
never earlier than its retention. Inside a chunk that was fetched, a bucket without rows had no
events (zero); outside fetched chunks values stay unknown (None), so missing data is never
reported as healthy.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.common import Entity, EntityKind, Scope, SourceKind
from app.domain.ids import series_id
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    ProgressReporter,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import AnalysisWindows, Exclusion, SourceInfo
from app.sources.base import HISTORY_DAYS
from app.sources.cloudflare.api import (
    DATASETS,
    SECONDS_PER_DAY,
    Chunk,
    CloudflareApi,
    DatasetSettings,
)
from app.sources.cloudflare.catalog import (
    BY_SIGNAL,
    CATALOG_VERSION,
    SIGNALS,
    CloudflareSignal,
    Query,
    display_query,
)

log = logging.getLogger("app.sources.cloudflare")

PROBE_SECONDS = 3600
HISTORY_PROBE_DAYS = 30
MAX_CHUNK_SECONDS = SECONDS_PER_DAY
MAX_LISTED_HOSTNAMES = 3
UNSUPPORTED_PROBE_KINDS = (SourceErrorKind.AUTH, SourceErrorKind.BAD_QUERY)
"""A refused timing/security probe means the plan or token lacks that dataset, not that the
zone is unreachable (settings and history already proved access)."""


def zone_entity(zone_id: str, hostnames: list[str], zone_name: str | None = None) -> Entity:
    """The zone as one entity; its domain only names it, so the key stays stable without it."""
    labels = {"zone_id": zone_id}
    if hostnames:
        labels["hostnames"] = ",".join(hostnames)
        shown = ", ".join(hostnames[:MAX_LISTED_HOSTNAMES])
        more = len(hostnames) - MAX_LISTED_HOSTNAMES
        name = f"{shown} (+{more})" if more > 0 else shown
    else:
        name = zone_name or f"zone {zone_id[:8]}…"
    key = "zone|" + "|".join(f"{k}={v}" for k, v in labels.items())
    return Entity(kind=EntityKind.ZONE, key=key, display_name=name, labels=labels)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


@dataclass(frozen=True)
class _Probe:
    status: CapabilityStatus
    reason: str | None = None


@dataclass
class _Fetched:
    """Chunks of one query kind: fetched ones with their range, and failures."""

    chunks: list[tuple[int, int, Chunk]]
    failures: list[SourceError]


class CloudflareMetricsSource:
    def __init__(
        self,
        api: CloudflareApi,
        zone_id: str,
        hostnames: list[str],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.zone_id = zone_id
        self.hostnames = hostnames
        self.entity = zone_entity(zone_id, hostnames)
        self.zone_name: str | None = None
        self._now = now or (lambda: datetime.now(UTC))
        self._settings: dict[str, DatasetSettings] | None = None
        self._named = False

    async def source_info(self) -> SourceInfo:
        return SourceInfo(base_url=self.api.base_url, backend=self.api.backend, version=None)

    # --- discovery ------------------------------------------------------------------------

    async def settings(self) -> dict[str, DatasetSettings]:
        """Dataset limits; documented defaults when the settings node cannot be read."""
        if self._settings is None:
            try:
                self._settings = await self.api.settings()
            except SourceError as exc:
                if exc.kind is not SourceErrorKind.BAD_QUERY:
                    raise
                log.warning("Cloudflare settings unavailable, using defaults: %s", exc.message)
                self._settings = {d: DatasetSettings(verified=False) for d in DATASETS}
        return self._settings

    async def resolve_zone_name(self) -> str | None:
        """Looks up the zone's domain once and names the entity after it."""
        if not self._named:
            self._named = True
            self.zone_name = await self.api.zone_name()
            self.entity = zone_entity(self.zone_id, self.hostnames, self.zone_name)
        return self.zone_name

    async def history_days(self, end: datetime) -> float | None:
        """Days since the zone's first day with requests in the last 30 days (whole zone)."""
        first = (end - timedelta(days=HISTORY_PROBE_DAYS)).date()
        try:
            days = await self.api.daily_requests(first, end.date())
        except SourceError as exc:
            if exc.kind is not SourceErrorKind.BAD_QUERY:
                raise
            return None
        seen = sorted(d for d, requests in days.items() if requests > 0)
        if not seen:
            return None
        start = datetime(seen[0].year, seen[0].month, seen[0].day, tzinfo=UTC)
        return round(min((end - start).total_seconds() / SECONDS_PER_DAY, HISTORY_PROBE_DAYS), 2)

    async def _probe(
        self, fetch: Callable[[int, int, int], Awaitable[Chunk]], at: int, what: str
    ) -> _Probe:
        try:
            await fetch(at - PROBE_SECONDS, at, 1)
        except SourceError as exc:
            if exc.kind not in UNSUPPORTED_PROBE_KINDS:
                raise
            return _Probe(CapabilityStatus.UNSUPPORTED, f"Cloudflare refused {what}: {exc.message}")
        return _Probe(CapabilityStatus.SUPPORTED)

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        self.api.begin(windows.end_time)
        settings = await self.settings()
        await self.resolve_zone_name()
        history = await self.history_days(windows.end_time)
        at = _ts(windows.end_time)

        probes = {Query.TRAFFIC: _Probe(CapabilityStatus.SUPPORTED)}
        probes[Query.TIMING] = await self._probe(
            self.api.timing,
            at,
            "timing quantiles (edge TTFB, origin response time need a Pro plan or higher)",
        )
        probes[Query.SECURITY] = await self._probe(self.api.security, at, "firewall events")

        caps = []
        for defn in SIGNALS:
            dataset = settings[defn.dataset]
            probe = probes[defn.query]
            status, reason = probe.status, probe.reason
            if not dataset.enabled:
                status = CapabilityStatus.UNSUPPORTED
                reason = f"{defn.dataset} is not available for this zone (plan or token)."
            caps.append(
                MetricCapability(
                    source=SourceKind.CLOUDFLARE,
                    family=defn.family,
                    signal=defn.signal,
                    status=status,
                    verified=dataset.verified,
                    required_metrics=[f"{defn.dataset}.{f}" for f in defn.fields],
                    required_labels=[],
                    observed_metrics=(
                        [f"{defn.dataset}.{f}" for f in defn.fields]
                        if status is CapabilityStatus.SUPPORTED
                        else []
                    ),
                    reason=reason,
                    history_days=history if status is CapabilityStatus.SUPPORTED else None,
                )
            )
        return caps

    # --- collection -----------------------------------------------------------------------

    def _chunks(
        self, settings: DatasetSettings, grid_start: int, end: int, step: int
    ) -> list[tuple[int, int]]:
        size = max(step, min(settings.max_duration, MAX_CHUNK_SECONDS) // step * step)
        earliest = -(-(end - settings.not_older_than) // step) * step + step  # inside retention
        start = max(grid_start, earliest)
        return [(s, min(s + size, end)) for s in range(start, end, size)]

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult:
        step = windows.step_seconds
        end = _ts(windows.end_time)
        grid_start = _ts(windows.end_time - timedelta(days=HISTORY_DAYS))
        size = (end - grid_start) // step
        supported = {
            c.signal
            for c in capabilities
            if c.signal in BY_SIGNAL and c.status is CapabilityStatus.SUPPORTED
        }
        queries = sorted({BY_SIGNAL[s].query for s in supported})
        settings = await self.settings()
        plans = {q: self._chunks(settings[_dataset(q)], grid_start, end, step) for q in queries}
        page_sizes = {q: settings[_dataset(q)].max_page_size for q in queries}

        total = sum(len(p) for p in plans.values())
        done = 0
        started = self._now()
        fetched: dict[Query, _Fetched] = {}

        async def fetch(query: Query, chunk_start: int, chunk_end: int) -> Chunk | SourceError:
            nonlocal done
            cancel.raise_if_cancelled()
            api_call = {
                Query.TRAFFIC: self.api.traffic,
                Query.TIMING: self.api.timing,
                Query.SECURITY: self.api.security,
            }[query]
            try:
                return await api_call(chunk_start, chunk_end, page_sizes[query])
            except SourceError as exc:
                return exc
            finally:
                done += 1
                await progress.update(
                    StageProgress(
                        stage=StageName.COLLECTION,
                        status=StageStatus.RUNNING,
                        started_at=started,
                        done=done,
                        total=total,
                        message=f"cloudflare {query.value}",
                    )
                )

        for query in queries:
            results = await asyncio.gather(*(fetch(query, s, e) for s, e in plans[query]))
            fetched[query] = _Fetched(
                chunks=[
                    (s, e, r)
                    for (s, e), r in zip(plans[query], results, strict=True)
                    if isinstance(r, Chunk)
                ],
                failures=[r for r in results if isinstance(r, SourceError)],
            )
            cancel.raise_if_cancelled()

        await progress.update(
            StageProgress(
                stage=StageName.COLLECTION,
                status=StageStatus.DONE,
                started_at=started,
                finished_at=self._now(),
                done=total,
                total=total,
            )
        )

        series: list[MetricSeries] = []
        exclusions: list[Exclusion] = []
        for query in queries:
            got = fetched[query]
            chunk_seconds = plans[query][0][1] - plans[query][0][0] if plans[query] else step
            signals = [s for s in SIGNALS if s.query is query and s.signal in supported]
            exclusions += _exclusions(query, got, signals, len(plans[query]))
            for defn in signals:
                series.append(
                    self._series(
                        defn,
                        got,
                        grid_start,
                        size,
                        step,
                        display_query(query, self.zone_id, self.hostnames, chunk_seconds),
                        windows,
                    )
                )

        log.info(
            "collected project=%s zone=%s catalog=%s series=%d exclusions=%d requests=%d",
            scope.project_id,
            self.zone_id,
            CATALOG_VERSION,
            len(series),
            len(exclusions),
            total,
        )
        return CollectionResult(series=series, exclusions=exclusions)

    def _series(
        self,
        defn: CloudflareSignal,
        got: _Fetched,
        grid_start: int,
        size: int,
        step: int,
        query: str,
        windows: AnalysisWindows,
    ) -> MetricSeries:
        values: list[float | None] = [None] * size
        intervals: list[float] = []
        for chunk_start, chunk_end, chunk in got.chunks:
            lo = max(0, (chunk_start - grid_start) // step)
            hi = min(size, (chunk_end - grid_start) // step)
            if defn.rate:  # no row in a fetched bucket means no events
                values[lo:hi] = [0.0] * (hi - lo)
            for bucket, value in chunk.values.get(defn.signal, {}).items():
                i = (bucket - grid_start) // step
                if lo <= i < hi:
                    values[i] = round(value / step if defn.rate else value, 6)
            if defn.query is not Query.SECURITY:
                intervals += [
                    v for b, v in chunk.sample_intervals.items() if chunk_start <= b < chunk_end
                ]

        observed = sum(v is not None for v in values)
        return MetricSeries(
            series_id=series_id(f"{defn.signal}\n{query}", self.entity.key),
            family=defn.family,
            signal=defn.signal,
            entity=self.entity,
            labels=dict(self.entity.labels),
            unit=defn.unit,
            query=query,
            step_seconds=step,
            start=datetime.fromtimestamp(grid_start, UTC),
            values=values,
            coverage=observed / size if size else 0.0,
            sample_interval=round(max(1.0, sum(intervals) / len(intervals)), 2)
            if intervals
            else None,
        )


def _dataset(query: Query) -> str:
    return next(s.dataset for s in SIGNALS if s.query is query)


def _exclusions(
    query: Query, got: _Fetched, signals: list[CloudflareSignal], planned: int
) -> list[Exclusion]:
    out: list[Exclusion] = []
    families = sorted({s.family for s in signals})
    if got.failures:
        first = got.failures[0]
        code = "query_timeout" if first.kind is SourceErrorKind.TIMEOUT else "query_failed"
        out += [
            Exclusion(
                code=code,
                family=family,
                message=f"Cloudflare {query.value}: {len(got.failures)} of {planned} chunks "
                f"failed ({first.kind.value}: {first.message}); those periods are unknown.",
            )
            for family in families
        ]
    if any(chunk.truncated for _, _, chunk in got.chunks):
        out += [
            Exclusion(
                code="series_truncated",
                family=family,
                message=f"Cloudflare {query.value}: a result reached the page-size limit; "
                "some buckets may be missing.",
            )
            for family in families
        ]
    return out
