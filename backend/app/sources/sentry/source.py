"""SentryMetricsSource: errors and transactions of Sentry projects as metric series (T017).

Each configured project is its own entity; the environment and tag filters apply to all of
them. The analysis window is fetched per project in chunks of at most seven days (2016
buckets, well under Sentry's 10,000 points per request), never before the project existed.
Inside a chunk that was fetched, a bucket without events is zero for counts and unknown for
durations; outside fetched chunks values stay unknown (None), so missing data is never
reported as healthy.

Error events are analysed per error kind: each project's top issues (``catalog.TOP_ISSUES``)
are their own entities, and the remaining events form an "(other errors)" entity (the
project's total minus those issues). When Sentry refuses to rank issues, a project's errors
are analysed together, as one series.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

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
from app.sources.sentry.api import SECONDS_PER_DAY, Chunk, IssueInfo, ProjectInfo, SentryApi
from app.sources.sentry.catalog import (
    BY_SIGNAL,
    CATALOG_VERSION,
    ERRORS_SIGNAL,
    RECENT_ISSUES,
    SIGNALS,
    TOP_ISSUES,
    TRANSACTION_DATASETS,
    Query,
    SentrySignal,
    display_query,
    issue_spec,
    query_spec,
)

log = logging.getLogger("app.sources.sentry")

PROBE_SECONDS = SECONDS_PER_DAY
HISTORY_PROBE_DAYS = 30
MAX_CHUNK_SECONDS = 7 * SECONDS_PER_DAY
ISSUE_CHUNK_SECONDS = 3 * SECONDS_PER_DAY
"""Grouped requests return a series per issue: 10 x 864 buckets stays under 10,000 points."""
OTHER_ERRORS = "(other errors)"
MAX_TITLE_CHARS = 120
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


def error_kind_entity(project: Entity, issue: str, title: str | None = None) -> Entity:
    """One error kind (a Sentry issue, or ``OTHER_ERRORS``) of a project's entity."""
    if title is None:
        name = issue
    else:
        short = title if len(title) <= MAX_TITLE_CHARS else title[: MAX_TITLE_CHARS - 1] + "…"
        name = f"{short} ({issue})"
    return Entity(
        kind=EntityKind.APPLICATION,
        key=f"{project.key}|issue={issue}",
        display_name=f"{project.display_name} · {name}",
        labels={**project.labels, "issue": issue},
    )


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

    @classmethod
    def of(cls, plan: list[tuple[int, int]], results: list[Chunk | SourceError]) -> _Fetched:
        return cls(
            planned=len(plan),
            chunks=[
                (s, e, r) for (s, e), r in zip(plan, results, strict=True) if isinstance(r, Chunk)
            ],
            failures=[r for r in results if isinstance(r, SourceError)],
        )


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

    async def _error_kinds(self, info: ProjectInfo, grid_start: int, end: int) -> list[IssueInfo]:
        """The project's top issues: those of the latest 24 h first, then those of the whole
        window. None when Sentry cannot rank them; the project's errors are then analysed
        together, as they are when Sentry refuses every grouped request."""
        try:
            recent = await self.api.top_issues([info.id], end - SECONDS_PER_DAY, end, RECENT_ISSUES)
            window = await self.api.top_issues([info.id], grid_start, end, TOP_ISSUES)
        except SourceError as exc:
            log.warning("sentry project %s: error kinds not ranked: %s", info.slug, exc.message)
            return []
        picked = {i.issue: i for i in recent}
        for issue in window:
            if len(picked) >= TOP_ISSUES:
                break
            picked.setdefault(issue.issue, issue)
        return list(picked.values())

    @staticmethod
    def _chunks(
        earliest: int, grid_start: int, end: int, step: int, max_seconds: int = MAX_CHUNK_SECONDS
    ) -> list[tuple[int, int]]:
        size = max_seconds // step * step
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
            info.id: self._chunks(self._earliest(info, grid_start), grid_start, end, step)
            for info in infos
        }
        kinds = (
            {info.id: await self._error_kinds(info, grid_start, end) for info in infos}
            if ERRORS_SIGNAL in supported
            else {}
        )
        kind_plans = {
            info.id: self._chunks(
                self._earliest(info, grid_start), grid_start, end, step, ISSUE_CHUNK_SECONDS
            )
            for info in infos
            if kinds.get(info.id)
        }

        total = sum(len(plan) for plan in plans.values()) * len(queries)
        total += sum(len(plan) for plan in kind_plans.values())
        done = 0
        started = self._now()

        async def fetch(what: str, call: Callable[[], Awaitable[Chunk]]) -> Chunk | SourceError:
            nonlocal done
            cancel.raise_if_cancelled()
            try:
                return await call()
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
                        message=f"sentry {what}",
                    )
                )

        fetched: dict[tuple[str, Query], _Fetched] = {}
        for query in queries:
            for info in infos:
                plan = plans[info.id]
                results = await asyncio.gather(
                    *(
                        fetch(query.value, partial(self.api.series, query, [info.id], s, e))
                        for s, e in plan
                    )
                )
                fetched[(info.id, query)] = _Fetched.of(plan, results)
                cancel.raise_if_cancelled()
        fetched_kinds: dict[str, _Fetched] = {}
        for pid, kind_plan in kind_plans.items():
            issues = [k.issue for k in kinds[pid]]
            results = await asyncio.gather(
                *(
                    fetch("error kinds", partial(self.api.issue_series, issues, [pid], s, e))
                    for s, e in kind_plan
                )
            )
            got_kinds = _Fetched.of(kind_plan, results)
            if not got_kinds.chunks and all(
                f.kind in UNSUPPORTED_PROBE_KINDS for f in got_kinds.failures
            ):
                first = got_kinds.failures[0].message if got_kinds.failures else ""
                log.warning("sentry project %s: error kinds refused: %s", pid, first)
                continue  # the project's errors are analysed together
            fetched_kinds[pid] = got_kinds
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
        grid = _Grid(grid_start, size, step)
        for query in queries:
            signals = [s for s in SIGNALS if s.query is query and s.signal in supported]
            spec = query_spec(query, self.api.transactions_dataset)
            for info in infos:
                got = fetched[(info.id, query)]
                exclusions += _exclusions(f"{query.value} of {info.slug}", got, signals)
                if query is Query.TRANSACTIONS and not _traced(got):
                    log.info("sentry project %s has no transactions; skipped", info.slug)
                    continue
                text = display_query(
                    spec, self.organization, info.id, self.environment, tags, _span(got, step)
                )
                entity = self.entity(info.slug)
                split = info.id in fetched_kinds and query is Query.ERRORS
                series += [
                    _series(d, entity, grid.values(d, got.chunks), grid, text)
                    for d in signals
                    if not (split and d.signal == ERRORS_SIGNAL)
                ]
                if split:
                    got_kinds = fetched_kinds[info.id]
                    exclusions += _exclusions(
                        f"error kinds of {info.slug}", got_kinds, [BY_SIGNAL[ERRORS_SIGNAL]]
                    )
                    series += self._kind_series(
                        info, kinds[info.id], got, got_kinds, grid, text, tags
                    )

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

    @staticmethod
    def _earliest(info: ProjectInfo, grid_start: int) -> int:
        return _ts(info.created) if info.created is not None else grid_start

    def _kind_series(
        self,
        info: ProjectInfo,
        kinds: list[IssueInfo],
        got: _Fetched,
        got_kinds: _Fetched,
        grid: _Grid,
        total_text: str,
        tags: list[tuple[str, str]],
    ) -> list[MetricSeries]:
        """One error series per issue, and the rest of the project's errors as another."""
        defn = BY_SIGNAL[ERRORS_SIGNAL]
        project = self.entity(info.slug)
        issues = [k.issue for k in kinds]
        text = display_query(
            issue_spec(issues),
            self.organization,
            info.id,
            self.environment,
            tags,
            _span(got_kinds, grid.step),
            top=len(issues),
        )
        out: list[MetricSeries] = []
        other = grid.values(defn, got.chunks)
        for kind in kinds:
            values = grid.values(
                defn, [(s, e, c.groups) for s, e, c in got_kinds.chunks], kind.issue
            )
            out.append(
                _series(
                    defn, error_kind_entity(project, kind.issue, kind.title), values, grid, text
                )
            )
            other = [
                None if t is None or v is None else max(0.0, t - v)
                for t, v in zip(other, values, strict=True)
            ]
        other_text = f"# All error events minus those of {', '.join(issues)}\n{total_text}"
        out.append(_series(defn, error_kind_entity(project, OTHER_ERRORS), other, grid, other_text))
        return out


@dataclass(frozen=True)
class _Grid:
    start: int
    size: int
    step: int

    def values(
        self,
        defn: SentrySignal,
        chunks: Sequence[tuple[int, int, Chunk | dict[str, dict[int, float]]]],
        key: str | None = None,
    ) -> list[float | None]:
        """Event counts (or values) per step of the signal, or of ``key`` of grouped chunks."""
        values: list[float | None] = [None] * self.size
        for chunk_start, chunk_end, chunk in chunks:
            lo = max(0, (chunk_start - self.start) // self.step)
            hi = min(self.size, (chunk_end - self.start) // self.step)
            if defn.zero_fill:
                values[lo:hi] = [0.0] * (hi - lo)
            found = chunk.values if isinstance(chunk, Chunk) else chunk
            for bucket, value in found.get(key or defn.signal, {}).items():
                i = (bucket - self.start) // self.step
                if lo <= i < hi:
                    values[i] = value
        return values


def _span(got: _Fetched, step: int) -> int:
    return got.chunks[0][1] - got.chunks[0][0] if got.chunks else step


def _series(
    defn: SentrySignal,
    entity: Entity,
    values: list[float | None],
    grid: _Grid,
    query: str,
) -> MetricSeries:
    if defn.rate:
        values = [None if v is None else round(v / grid.step, 6) for v in values]
    else:
        values = [None if v is None else round(v, 6) for v in values]
    observed = sum(v is not None for v in values)
    return MetricSeries(
        series_id=series_id(f"{defn.signal}\n{query}", entity.key),
        family=defn.family,
        signal=defn.signal,
        entity=entity,
        labels=dict(entity.labels),
        unit=defn.unit,
        query=query,
        step_seconds=grid.step,
        start=datetime.fromtimestamp(grid.start, UTC),
        values=values,
        coverage=observed / grid.size if grid.size else 0.0,
    )


def _traced(got: _Fetched) -> bool:
    """Whether a project sent any transaction in the fetched window. With nothing fetched,
    its series are kept so the failed periods are reported as unknown."""
    if not got.chunks:
        return True
    return any(any(c.values.get(TRANSACTION_COUNT, {}).values()) for _, _, c in got.chunks)


def _exclusions(what: str, got: _Fetched, signals: list[SentrySignal]) -> list[Exclusion]:
    if not got.failures:
        return []
    first = got.failures[0]
    code = "query_timeout" if first.kind is SourceErrorKind.TIMEOUT else "query_failed"
    return [
        Exclusion(
            code=code,
            family=family,
            message=f"Sentry {what}: {len(got.failures)} of {got.planned} "
            f"chunks failed ({first.kind.value}: {first.message}); those periods are unknown.",
        )
        for family in sorted({s.family for s in signals})
    ]
