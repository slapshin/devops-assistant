"""SentryMetricsSource: errors and transactions of Sentry projects as metric series (T017).

Each configured project is its own entity; the environment and tag filters apply to all of
them. The analysis window is fetched per project in chunks of at most seven days (2016
buckets, well under Sentry's 10,000 points per request), never before the project existed.
Inside a chunk that was fetched, a bucket without events is zero for counts and unknown for
durations; outside fetched chunks values stay unknown (None), so missing data is never
reported as healthy.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
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
from app.domain.projects import SentryTag
from app.domain.report import AnalysisWindows, Exclusion, SourceInfo
from app.sources.base import HISTORY_DAYS
from app.sources.sentry.api import SECONDS_PER_DAY, Chunk, ProjectInfo, SentryApi
from app.sources.sentry.catalog import (
    BY_SIGNAL,
    CATALOG_VERSION,
    SIGNALS,
    TRANSACTION_DATASETS,
    Query,
    SentrySignal,
    display_query,
    query_spec,
)

log = logging.getLogger("app.sources.sentry")

PROBE_SECONDS = SECONDS_PER_DAY
HISTORY_PROBE_DAYS = 30
MAX_CHUNK_SECONDS = 7 * SECONDS_PER_DAY
UNSUPPORTED_PROBE_KINDS = (SourceErrorKind.AUTH, SourceErrorKind.BAD_QUERY)
"""A refused probe means the plan, version or token lacks that dataset, not that Sentry is
unreachable (reading the projects already proved access)."""
TRANSACTION_COUNT = "sentry_transactions"


def application_entity(
    organization: str, project: str, environment: str | None, tags: Sequence[SentryTag] = ()
) -> Entity:
    labels = {"organization": organization, "project": project}
    if environment:
        labels["environment"] = environment
    if tags:
        labels["tags"] = ",".join(f"{t.key}={t.value}" for t in tags)
    name = f"{project} ({environment})" if environment else project
    key = "application|" + "|".join(f"{k}={v}" for k, v in labels.items())
    return Entity(kind=EntityKind.APPLICATION, key=key, display_name=name, labels=labels)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


@dataclass(frozen=True)
class _Probe:
    status: CapabilityStatus
    reason: str | None = None


@dataclass
class _Fetched:
    """Chunks of one (project, query): fetched ones with their range, and failures."""

    planned: int
    chunks: list[tuple[int, int, Chunk]]
    failures: list[SourceError]


class SentryMetricsSource:
    def __init__(
        self,
        api: SentryApi,
        organization: str,
        projects: Sequence[str],
        environment: str | None = None,
        tags: Sequence[SentryTag] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.organization = organization
        self.projects = list(projects)
        self.environment = environment
        self.tags = list(tags)
        self._now = now or (lambda: datetime.now(UTC))
        self._infos: list[ProjectInfo] | None = None
        self.last_day: dict[Query, Chunk] = {}
        """The latest 24 h of each probed query, all projects together (connection test)."""

    async def source_info(self) -> SourceInfo:
        return SourceInfo(base_url=self.api.base_url, backend=self.api.backend, version=None)

    def entity(self, project: str) -> Entity:
        return application_entity(self.organization, project, self.environment, self.tags)

    # --- discovery ------------------------------------------------------------------------

    async def infos(self) -> list[ProjectInfo]:
        if self._infos is None:
            self._infos = await self.api.projects()
        return self._infos

    @staticmethod
    def history_days(infos: Sequence[ProjectInfo], end: datetime) -> float | None:
        """Age of the oldest project, up to 30 days."""
        created = [i.created for i in infos if i.created is not None]
        if not created:
            return None
        days = (end - min(created)).total_seconds() / SECONDS_PER_DAY
        return round(max(0.0, min(days, HISTORY_PROBE_DAYS)), 2)

    async def _probe(self, query: Query, ids: list[str], at: int, what: str) -> _Probe:
        try:
            self.last_day[query] = await self.api.series(query, ids, at - PROBE_SECONDS, at)
        except SourceError as exc:
            if exc.kind not in UNSUPPORTED_PROBE_KINDS:
                raise
            return _Probe(CapabilityStatus.UNSUPPORTED, f"Sentry refused {what}: {exc.message}")
        return _Probe(CapabilityStatus.SUPPORTED)

    async def _probe_transactions(self, ids: list[str], at: int) -> _Probe:
        """Use the first dataset that is accepted and has transactions in the last 24 h.

        sentry.io stores transactions as spans; self-hosted Sentry (25.x) keeps them in the
        classic transactions dataset, while its ``spans`` dataset may be refused or empty.
        """
        refused: list[str] = []
        for dataset in TRANSACTION_DATASETS:
            self.api.transactions_dataset = dataset
            probe = await self._probe(Query.TRANSACTIONS, ids, at, f"the {dataset} dataset")
            if probe.status is not CapabilityStatus.SUPPORTED:
                refused.append(probe.reason or dataset)
                continue
            if any(self.last_day[Query.TRANSACTIONS].values.get(TRANSACTION_COUNT, {}).values()):
                return probe
        self.api.transactions_dataset = TRANSACTION_DATASETS[0]
        if len(refused) == len(TRANSACTION_DATASETS):
            return _Probe(CapabilityStatus.UNSUPPORTED, "; ".join(refused))
        return _Probe(
            CapabilityStatus.UNSUPPORTED,
            "No transactions in the last 24 h; tracing (performance monitoring) appears "
            "not to be set up for these projects.",
        )

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        self.api.begin(windows.end_time)
        infos = await self.infos()
        history = self.history_days(infos, windows.end_time)
        ids = [i.id for i in infos]
        at = _ts(windows.end_time)

        probes = {
            Query.ERRORS: await self._probe(Query.ERRORS, ids, at, "error events"),
            Query.UNHANDLED: await self._probe(Query.UNHANDLED, ids, at, "unhandled errors"),
            Query.TRANSACTIONS: await self._probe_transactions(ids, at),
        }

        dataset = self.api.transactions_dataset
        caps = []
        for defn in SIGNALS:
            probe = probes[defn.query]
            supported = probe.status is CapabilityStatus.SUPPORTED
            spec = query_spec(defn.query, dataset)
            metric = f"events-timeseries:{spec.dataset}:{defn.aggregate(dataset)}"
            caps.append(
                MetricCapability(
                    source=SourceKind.SENTRY,
                    family=defn.family,
                    signal=defn.signal,
                    status=probe.status,
                    verified=True,
                    required_metrics=[metric],
                    required_labels=[],
                    observed_metrics=[metric] if supported else [],
                    reason=probe.reason,
                    history_days=history if supported else None,
                )
            )
        return caps

    # --- collection -----------------------------------------------------------------------

    @staticmethod
    def _chunks(earliest: int, grid_start: int, end: int, step: int) -> list[tuple[int, int]]:
        size = MAX_CHUNK_SECONDS // step * step
        first = -(-earliest // step) * step  # the first whole bucket after creation
        start = max(grid_start, first)
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
        infos = await self.infos()
        plans = {
            info.id: self._chunks(
                _ts(info.created) if info.created is not None else grid_start,
                grid_start,
                end,
                step,
            )
            for info in infos
        }

        total = sum(len(plan) for plan in plans.values()) * len(queries)
        done = 0
        started = self._now()

        async def fetch(query: Query, pid: str, lo: int, hi: int) -> Chunk | SourceError:
            nonlocal done
            cancel.raise_if_cancelled()
            try:
                return await self.api.series(query, [pid], lo, hi)
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
                        message=f"sentry {query.value}",
                    )
                )

        fetched: dict[tuple[str, Query], _Fetched] = {}
        for query in queries:
            for info in infos:
                plan = plans[info.id]
                results = await asyncio.gather(*(fetch(query, info.id, s, e) for s, e in plan))
                fetched[(info.id, query)] = _Fetched(
                    planned=len(plan),
                    chunks=[
                        (s, e, r)
                        for (s, e), r in zip(plan, results, strict=True)
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
        tags = [(t.key, t.value) for t in self.tags]
        for query in queries:
            signals = [s for s in SIGNALS if s.query is query and s.signal in supported]
            spec = query_spec(query, self.api.transactions_dataset)
            for info in infos:
                got = fetched[(info.id, query)]
                exclusions += _exclusions(query, info.slug, got, signals)
                if query is Query.TRANSACTIONS and not _traced(got):
                    log.info("sentry project %s has no transactions; skipped", info.slug)
                    continue
                chunk_seconds = got.chunks[0][1] - got.chunks[0][0] if got.chunks else step
                text = display_query(
                    spec, self.organization, info.id, self.environment, tags, chunk_seconds
                )
                entity = self.entity(info.slug)
                series += [_series(d, entity, got, grid_start, size, step, text) for d in signals]

        log.info(
            "collected project=%s sentry=%s projects=%d catalog=%s series=%d exclusions=%d "
            "requests=%d",
            scope.project_id,
            self.organization,
            len(infos),
            CATALOG_VERSION,
            len(series),
            len(exclusions),
            total,
        )
        return CollectionResult(series=series, exclusions=exclusions)


def _series(
    defn: SentrySignal,
    entity: Entity,
    got: _Fetched,
    grid_start: int,
    size: int,
    step: int,
    query: str,
) -> MetricSeries:
    values: list[float | None] = [None] * size
    for chunk_start, chunk_end, chunk in got.chunks:
        lo = max(0, (chunk_start - grid_start) // step)
        hi = min(size, (chunk_end - grid_start) // step)
        if defn.zero_fill:
            values[lo:hi] = [0.0] * (hi - lo)
        for bucket, value in chunk.values.get(defn.signal, {}).items():
            i = (bucket - grid_start) // step
            if lo <= i < hi:
                values[i] = round(value / step if defn.rate else value, 6)

    observed = sum(v is not None for v in values)
    return MetricSeries(
        series_id=series_id(f"{defn.signal}\n{query}", entity.key),
        family=defn.family,
        signal=defn.signal,
        entity=entity,
        labels=dict(entity.labels),
        unit=defn.unit,
        query=query,
        step_seconds=step,
        start=datetime.fromtimestamp(grid_start, UTC),
        values=values,
        coverage=observed / size if size else 0.0,
    )


def _traced(got: _Fetched) -> bool:
    """Whether a project sent any transaction in the fetched window. With nothing fetched,
    its series are kept so the failed periods are reported as unknown."""
    if not got.chunks:
        return True
    return any(any(c.values.get(TRANSACTION_COUNT, {}).values()) for _, _, c in got.chunks)


def _exclusions(
    query: Query, project: str, got: _Fetched, signals: list[SentrySignal]
) -> list[Exclusion]:
    if not got.failures:
        return []
    first = got.failures[0]
    code = "query_timeout" if first.kind is SourceErrorKind.TIMEOUT else "query_failed"
    return [
        Exclusion(
            code=code,
            family=family,
            message=f"Sentry {query.value} of {project}: {len(got.failures)} of {got.planned} "
            f"chunks failed ({first.kind.value}: {first.message}); those periods are unknown.",
        )
        for family in sorted({s.family for s in signals})
    ]
