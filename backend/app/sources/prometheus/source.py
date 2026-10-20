"""PrometheusMetricsSource: the read-only, scope-enforcing MetricsSource (T004)."""

import contextlib
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.common import Entity, EntityKind, MatchOp, Scope, SignalFamily
from app.domain.custom_rules import (
    PREVIEW_MAX_SERIES,
    CustomRule,
    CustomRulePreview,
    CustomRulePreviewSeries,
)
from app.domain.diagnostics import ScopeDiagnostics
from app.domain.ids import series_id
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    EntityMapping,
    MappingKind,
    ProgressReporter,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import AnalysisWindows, Exclusion, SourceInfo
from app.sources.base import HISTORY_DAYS
from app.sources.prometheus.catalog import (
    ALL_REQUIRED_METRICS,
    CATALOG,
    CATALOG_VERSION,
    TRAFFIC_SIGNALS,
    SignalDef,
)
from app.sources.prometheus.client import PrometheusClient, RangeResult
from app.sources.prometheus.custom import custom_identity, custom_signal, custom_signals
from app.sources.prometheus.diagnostics import (
    matcher_checks,
    prometheus_diagnostics,
    varied_samples,
)
from app.sources.prometheus.promql import ScopeViolation, assert_scoped, scope_matchers

log = logging.getLogger("app.sources.prometheus")

OTHER_ROUTES = "(other routes)"
SECONDS_PER_DAY = 86400
HISTORY_PROBE_DAYS = 30
HISTORY_PROBE_STEP_SECONDS = 3600

MEAN_LATENCY_SIGNAL = "http_latency_mean"
_FALLBACK_REASON_PREFIX = "Fallback"
_MEAN_LATENCY_FALLBACK_REASON = f"{_FALLBACK_REASON_PREFIX} only; percentiles are available."
# service_instance_id and cAdvisor container ids are matched on Docker's 12-char short id.
CONTAINER_ID_PREFIX_LENGTH = 12
# Two targets written into one series (e.g. replicas sharing a fixed `instance`) interleave their
# counters: every switch reads as a reset and `rate()` turns the whole counter into growth. A
# restarting process resets a few times a day; colliding targets reset in up to every other
# sample. Checked over the latest day, the window the findings come from.
COLLISION_WINDOW = "1d"
COLLISION_MIN_RESETS = 10
COLLISION_RESET_RATIO = 0.1
_RATED_SELECTOR = re.compile(
    r'\b(?:rate|irate|increase)\(([A-Za-z_:][\w:]*)(\{(?:[^{}"]|"(?:[^"\\]|\\.)*")*\})\['
)


@dataclass(frozen=True)
class CollectionBudget:
    max_series_per_query: int = 500
    max_series_per_job: int = 5000
    top_routes_per_service: int = 20


def scope_labels(scope: Scope) -> dict[str, str]:
    """Labels every scoped series carries with a known value (the equality matchers)."""
    return {m.name: m.value for m in scope.matchers if m.op is MatchOp.EQUAL}


def entity_for(defn: SignalDef, labels: dict[str, str], scope: Scope | None = None) -> Entity:
    """``scope`` is needed for custom rules without identity labels (their labels minus it)."""
    identity = defn.identity
    if defn.entity_kind is EntityKind.CUSTOM and scope is not None:
        identity = custom_identity(defn, labels, scope)
    # Absent optional labels are left out, so entities without them keep their key and labels.
    ident = {
        k: labels.get(k, "") for k in identity if k not in defn.optional_labels or labels.get(k)
    }
    key = f"{defn.entity_kind.value}|" + "|".join(f"{k}={v}" for k, v in ident.items())
    match defn.entity_kind:
        case EntityKind.NODE:
            name = f"node · {ident['instance']}"
        case EntityKind.FILESYSTEM:
            name = f"{ident['instance']} · {ident['mountpoint']} ({ident['device']})"
        case EntityKind.DISK | EntityKind.NETWORK_INTERFACE:
            name = f"{ident['instance']} · {ident['device']}"
        case EntityKind.CONTAINER:
            name = f"{ident['container']} @ {ident['instance']}"
        case EntityKind.SERVICE:
            name = ident["service_name"]
        case EntityKind.PROXY | EntityKind.DATABASE | EntityKind.PROCESS:
            # Traefik routers and services are often named alike; show each value once.
            name = " · ".join(dict.fromkeys(v for v in ident.values() if v))
        case EntityKind.CUSTOM:
            name = " · ".join(v for v in ident.values() if v) or defn.description or defn.signal
        case EntityKind.UPSTREAM:
            name = f"{ident['job']} · " + " → ".join(v for k, v in ident.items() if k != "job")
        case EntityKind.ROUTE if "rpc_method" in ident:
            name = f"{ident['job']} · {ident['rpc_method']}"
        case _:
            name = f"{ident['job']} · {ident['http_request_method']} {ident['http_route']}".strip()
            if host := ident.get("server_address"):
                name += f" @ {host}"
    return Entity(kind=defn.entity_kind, key=key, display_name=name, labels=ident)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


class PrometheusMetricsSource:
    def __init__(
        self,
        client: PrometheusClient,
        budget: CollectionBudget | None = None,
        now: Callable[[], datetime] | None = None,
        custom_rules: Sequence[CustomRule] = (),
    ) -> None:
        self.client = client
        self.budget = budget or CollectionBudget()
        self._now = now or (lambda: datetime.now(UTC))
        self.custom_rules = list(custom_rules)
        self.custom = custom_signals(self.custom_rules)
        self.catalog = (*CATALOG, *self.custom)

    # --- discovery ------------------------------------------------------------------------

    async def source_info(self) -> SourceInfo:
        version = None
        with contextlib.suppress(SourceError):
            version = (await self.client.buildinfo()).get("version")
        return SourceInfo(base_url=self.client.base_url, backend=None, version=version)

    async def _instant(self, query: str, scope: Scope, at: int) -> list[RangeResult]:
        assert_scoped(query, scope, ())
        return await self.client.query(query, at)

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        at = _ts(windows.end_time)
        s = scope_matchers(scope)
        names_regex = self._names_regex()
        present = {
            r.labels.get("__name__", "")
            for r in await self._instant(
                f'count by (__name__) ({{__name__=~"{names_regex}", {s}}})', scope, at
            )
        }
        history_days = await self._history_days(scope, at)

        gate_cache: dict[str, bool] = {}
        caps: list[MetricCapability] = []
        for defn in CATALOG:
            caps.append(await self._capability(defn, scope, at, present, history_days, gate_cache))
        for defn in self.custom:
            caps.append(await self._custom_capability(defn, scope, at, present, history_days))
        return _mark_mean_latency_fallback(caps)

    def _names_regex(self) -> str:
        """Every metric the catalog and the custom rules need, as one name regex."""
        custom_metrics = {m for d in self.custom for m in d.required_metrics}
        return "|".join(sorted({*ALL_REQUIRED_METRICS, *custom_metrics}))

    async def _history_days(self, scope: Scope, at: int) -> float | None:
        query = f"count(up{{{scope_matchers(scope)}}})"
        assert_scoped(query, scope, ("up",))
        res = await self.client.query_range(
            query, at - HISTORY_PROBE_DAYS * SECONDS_PER_DAY, at, HISTORY_PROBE_STEP_SECONDS
        )
        stamps = [ts for r in res for ts, v in r.samples if v]
        return round((at - min(stamps)) / SECONDS_PER_DAY, 2) if stamps else None

    async def _capability(
        self,
        defn: SignalDef,
        scope: Scope,
        at: int,
        present: set[str],
        history_days: float | None,
        gate_cache: dict[str, bool],
    ) -> MetricCapability:
        missing = [m for m in defn.required_metrics if m not in present]
        base = {
            "family": defn.family,
            "signal": defn.signal,
            "verified": True,
            "required_metrics": list(defn.required_metrics),
            "required_labels": [*scope.label_names, *defn.required_labels],
        }
        if missing:
            return MetricCapability(
                **base,
                status=CapabilityStatus.UNSUPPORTED,
                reason=f"Not found in this scope: {', '.join(missing)}.",
            )
        observed = list(defn.required_metrics)
        for gate in defn.gates:
            query = gate.template.render(scope)
            if query not in gate_cache:
                res = await self._instant(query, scope, at)
                gate_cache[query] = any((r.samples[0][1] or 0) > 0 for r in res)
            if not gate_cache[query]:
                return MetricCapability(
                    **base,
                    status=CapabilityStatus.UNSUPPORTED,
                    observed_metrics=observed,
                    reason=gate.unsupported_reason,
                )
        status, reason = CapabilityStatus.SUPPORTED, None
        if defn.entity_kind is EntityKind.CONTAINER:
            status, reason = await self._container_coverage(scope, at)
        return MetricCapability(
            **base,
            status=status,
            observed_metrics=observed,
            reason=reason,
            history_days=history_days,
        )

    async def _custom_capability(
        self,
        defn: SignalDef,
        scope: Scope,
        at: int,
        present: set[str],
        history_days: float | None,
    ) -> MetricCapability:
        """A custom rule is supported when its metrics exist and its query runs."""
        base = {
            "family": defn.family,
            "signal": defn.signal,
            "verified": True,
            "required_metrics": list(defn.required_metrics),
            "required_labels": [*scope.label_names, *defn.identity],
        }

        def unsupported(reason: str) -> MetricCapability:
            return MetricCapability(**base, status=CapabilityStatus.UNSUPPORTED, reason=reason)

        if missing := [m for m in defn.required_metrics if m not in present]:
            return unsupported(f"Not found in this scope: {', '.join(missing)}.")
        try:
            results = await self._instant(defn.query.render(scope), scope, at)
        except ScopeViolation as exc:
            return unsupported(f"Query cannot be scoped: {exc}")
        except SourceError as exc:
            return unsupported(f"Query failed: {exc.message}")
        if not results and not defn.required_metrics:
            return unsupported("The query returned no series.")
        return MetricCapability(
            **base,
            status=CapabilityStatus.SUPPORTED,
            observed_metrics=list(defn.required_metrics),
            history_days=history_days,
        )

    async def _container_coverage(
        self, scope: Scope, at: int
    ) -> tuple[CapabilityStatus, str | None]:
        s = scope_matchers(scope)
        all_hosts = {
            r.labels.get("instance", "")
            for r in await self._instant(
                f"count by (instance) (container_cpu_usage_seconds_total{{{s}}})", scope, at
            )
        }
        named = {
            r.labels.get("instance", "")
            for r in await self._instant(
                f'count by (instance) (container_cpu_usage_seconds_total{{{s}, name!=""}})',
                scope,
                at,
            )
        }
        if missing := sorted(all_hosts - named):
            return CapabilityStatus.PARTIAL, (
                f"No named containers reported on {', '.join(missing)} (root cgroup only)."
            )
        return CapabilityStatus.SUPPORTED, None

    async def preview_rule(
        self, rule: CustomRule, scope: Scope, end_time: datetime, step: int
    ) -> CustomRulePreview:
        """The rule's series over the 24 h before ``end_time``; errors become a message."""
        defn = custom_signal(rule)
        start = end_time - timedelta(days=1)
        try:
            query = defn.query.render(scope)
        except ScopeViolation as exc:
            return failed_preview(None, start, step, f"Query cannot be scoped: {exc}")
        try:
            results = await self.client.query_range(query, _ts(start) + step, _ts(end_time), step)
        except SourceError as exc:
            return failed_preview(query, start, step, exc.message)

        results = self._scoped_results(results, scope, defn, [])
        size = SECONDS_PER_DAY // step
        grids = [(dict(r.labels), _to_grid(r, _ts(start), step, size)) for r in results]
        return preview_from_grids(defn, scope, query, start, step, grids)

    # --- collection -----------------------------------------------------------------------

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult:
        step = windows.step_seconds
        grid_start = windows.end_time - timedelta(days=HISTORY_DAYS)
        grid = _Grid(
            start=grid_start,
            step=step,
            size=int((windows.end_time - grid_start).total_seconds()) // step,
        )
        plan = _collection_plan(capabilities, self.catalog)

        exclusions: list[Exclusion] = []
        series: list[MetricSeries] = []
        top_routes: dict[str, set[tuple[str, ...]]] = {}
        collisions = _Collisions()
        samples: dict[tuple[SignalFamily, str], dict[str, str]] = {}
        started = self._now()
        for i, defn in enumerate(plan):
            cancel.raise_if_cancelled()
            await progress.update(
                StageProgress(
                    stage=StageName.COLLECTION,
                    status=StageStatus.RUNNING,
                    started_at=started,
                    done=i,
                    total=len(plan),
                    message=defn.signal,
                )
            )
            if len(series) >= self.budget.max_series_per_job:
                exclusions.append(
                    Exclusion(
                        code="series_truncated",
                        family=defn.family,
                        message=f"{defn.signal}: job series budget "
                        f"({self.budget.max_series_per_job}) reached; not collected.",
                    )
                )
                continue
            series += await self._collect_signal(
                defn, scope, windows.end_time, grid, exclusions, top_routes, collisions, samples
            )
        exclusions += collisions.exclusions()
        await progress.update(
            StageProgress(
                stage=StageName.COLLECTION,
                status=StageStatus.DONE,
                started_at=started,
                finished_at=self._now(),
                done=len(plan),
                total=len(plan),
            )
        )

        mappings = await self._mappings(scope, windows, exclusions)
        cancel.raise_if_cancelled()
        diagnostics = await self._diagnostics(scope, windows, plan, series, samples)

        log.info(
            "collected project=%s matchers=%s catalog=%s series=%d exclusions=%d requests=%d",
            scope.project_id,
            scope_matchers(scope),
            CATALOG_VERSION,
            len(series),
            len(exclusions),
            self.client.request_count,
        )
        return CollectionResult(
            series=series,
            exclusions=exclusions,
            mappings=mappings,
            custom_rules=self.custom_rules,
            diagnostics=[diagnostics],
        )

    async def _diagnostics(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        plan: Sequence[SignalDef],
        series: Sequence[MetricSeries],
        samples: dict[tuple[SignalFamily, str], dict[str, str]],
    ) -> ScopeDiagnostics:
        """What the matchers selected; checks run after collection and never fail it."""
        names_regex = self._names_regex()
        discovery = f'count by (__name__) ({{__name__=~"{names_regex}", {scope_matchers(scope)}}})'
        checks, notes, requests = await matcher_checks(
            self.client, scope, windows.end_time, names_regex
        )
        queries = [discovery, *requests, *(d.query.render(scope) for d in plan)]
        return prometheus_diagnostics(series, varied_samples(samples), checks, notes, queries)

    async def _collect_signal(
        self,
        defn: SignalDef,
        scope: Scope,
        end_time: datetime,
        grid: _Grid,
        exclusions: list[Exclusion],
        top_routes: dict[str, set[tuple[str, ...]]],
        collisions: _Collisions,
        samples: dict[tuple[SignalFamily, str], dict[str, str]],
    ) -> list[MetricSeries]:
        """Query one signal over the grid; failures, truncation and invalid data become
        exclusions."""
        query = defn.query.render(scope)
        grid_start = _ts(grid.start)
        try:
            results = await self.client.query_range(
                query, grid_start + grid.step, _ts(end_time), grid.step
            )
        except SourceError as exc:
            code = "query_timeout" if exc.kind is SourceErrorKind.TIMEOUT else "query_failed"
            exclusions.append(
                Exclusion(code=code, family=defn.family, message=f"{defn.signal}: {exc.message}")
            )
            return []

        results = self._scoped_results(results, scope, defn, exclusions)
        results = await self._drop_collisions(defn, scope, query, end_time, results, collisions)
        if results:
            samples.setdefault((defn.family, defn.signal), dict(results[0].labels))
        max_series = self.budget.max_series_per_query
        if len(results) > max_series:
            exclusions.append(
                Exclusion(
                    code="series_truncated",
                    family=defn.family,
                    message=f"{defn.signal}: {len(results)} series exceed the per-query budget "
                    f"({max_series}); kept the first {max_series} by identity.",
                )
            )
            results = results[:max_series]

        grids = [(dict(r.labels), _to_grid(r, grid_start, grid.step, grid.size)) for r in results]
        if defn.route_level:
            grids = self._limit_routes(defn, grids, top_routes, grid.size)

        out: list[MetricSeries] = []
        for labels, values in grids:
            entity = entity_for(defn, dict(labels), scope)
            if invalid := _drop_above(values, defn.max_value):
                exclusions.append(
                    Exclusion(
                        code="invalid_values",
                        family=defn.family,
                        message=f"{defn.signal}: {entity.display_name}: {invalid} samples above "
                        f"the possible maximum {defn.max_value:g} dropped as invalid data.",
                    )
                )
            out.append(
                MetricSeries(
                    series_id=series_id(query, entity.key),
                    family=defn.family,
                    signal=defn.signal,
                    entity=entity,
                    labels={**scope_labels(scope), **entity.labels},
                    unit=defn.unit,
                    query=query,
                    step_seconds=grid.step,
                    start=grid.start,
                    values=values,
                    coverage=sum(v is not None for v in values) / grid.size,
                )
            )
        return out

    async def _drop_collisions(
        self,
        defn: SignalDef,
        scope: Scope,
        query: str,
        end_time: datetime,
        results: list[RangeResult],
        collisions: _Collisions,
    ) -> list[RangeResult]:
        """Drop entities whose rated counters collide; they are disclosed once per entity."""
        flagged: set[tuple[str, ...]] = set()
        for probe in collision_probes(defn, query):
            if probe not in collisions.probes:
                collisions.probes[probe] = await self._colliding(probe, scope, end_time, defn)
            flagged |= collisions.probes[probe]
        if not flagged:
            return results

        kept: list[RangeResult] = []
        for r in results:
            if _identity(r.labels, defn) not in flagged:
                kept.append(r)
                continue
            entity = entity_for(defn, dict(r.labels), scope)
            _, _, signals = collisions.dropped.setdefault(entity.key, (entity, defn.family, []))
            if defn.signal not in signals:
                signals.append(defn.signal)
        return kept

    async def _colliding(
        self, probe: str, scope: Scope, end_time: datetime, defn: SignalDef
    ) -> frozenset[tuple[str, ...]]:
        """Identities over the reset ratio; a failed probe flags nothing."""
        try:
            results = await self._instant(probe, scope, _ts(end_time))
        except SourceError as exc:
            log.warning("counter collision probe failed for %s: %s", defn.signal, exc.message)
            return frozenset()
        return frozenset(
            _identity(r.labels, defn)
            for r in results
            if (r.samples[0][1] or 0) >= COLLISION_RESET_RATIO
        )

    @staticmethod
    def _scoped_results(
        results: list[RangeResult], scope: Scope, defn: SignalDef, exclusions: list[Exclusion]
    ) -> list[RangeResult]:
        """Defence in depth: drop any returned series whose labels leave the scope."""
        kept = [
            r
            for r in results
            if all(m.name not in r.labels or m.matches(r.labels[m.name]) for m in scope.matchers)
        ]
        if len(kept) != len(results):
            exclusions.append(
                Exclusion(
                    code="query_failed",
                    family=defn.family,
                    message=f"{defn.signal}: dropped {len(results) - len(kept)} foreign series",
                )
            )
        if not defn.identity:  # a custom rule identified by all its labels
            return sorted(kept, key=lambda r: sorted(r.labels.items()))
        return sorted(kept, key=lambda r: tuple(r.labels.get(k, "") for k in defn.identity))

    def _limit_routes(
        self,
        defn: SignalDef,
        grids: list[tuple[dict[str, str], list[float | None]]],
        top_routes: dict[str, set[tuple[str, ...]]],
        n: int,
    ) -> list[tuple[dict[str, str], list[float | None]]]:
        """Keep the top routes per service by 14-day volume; sum the rest (rates only)."""
        route_labels = defn.identity[1:]
        rollup_label = defn.required_labels[1]
        traffic = defn.traffic or defn.signal
        # The traffic signal is collected first, so it decides the routes for its operands.
        if defn.signal == traffic:
            top_routes.update(self._rank_top_routes(traffic, grids, route_labels, n))

        kept: list[tuple[dict[str, str], list[float | None]]] = []
        other: dict[str, list[float | None]] = {}
        for labels, values in grids:
            job = labels.get("job", "")
            allowed = top_routes.get(f"{traffic}|{job}")
            route = tuple(labels.get(k, "") for k in route_labels)
            if allowed is None or route in allowed:
                kept.append((labels, values))
            elif defn.unit.value.endswith("per_second"):
                # Only rates add up meaningfully; other units drop the long tail.
                acc = other.setdefault(job, [None] * n)
                for i, v in enumerate(values):
                    if v is not None:
                        acc[i] = (acc[i] or 0.0) + v

        for job, values in sorted(other.items()):
            # The first required route label names the rollup; the rest (method, host, ...) are
            # empty, and empty optional labels are dropped from the entity.
            labels = {"job": job, **{k: "" for k in route_labels}, rollup_label: OTHER_ROUTES}
            kept.append((labels, values))
        return kept

    def _rank_top_routes(
        self,
        traffic: str,
        grids: list[tuple[dict[str, str], list[float | None]]],
        route_labels: tuple[str, ...],
        n: int,
    ) -> dict[str, set[tuple[str, ...]]]:
        """Top routes per job by request volume over the trend range (second half of the grid)."""
        trend_start = n // 2
        per_job: dict[str, list[tuple[float, tuple[str, ...]]]] = defaultdict(list)
        for labels, values in grids:
            volume = sum(v for v in values[trend_start:] if v is not None)
            route = tuple(labels.get(k, "") for k in route_labels)
            per_job[labels.get("job", "")].append((volume, route))

        top: dict[str, set[tuple[str, ...]]] = {}
        for job, items in per_job.items():
            items.sort(key=lambda x: (-x[0], x[1]))
            top[f"{traffic}|{job}"] = {r for _, r in items[: self.budget.top_routes_per_service]}
        return top

    async def _mappings(
        self, scope: Scope, windows: AnalysisWindows, exclusions: list[Exclusion]
    ) -> list[EntityMapping]:
        at = _ts(windows.end_time)
        out: set[tuple[MappingKind, str, str]] = set()
        try:
            out |= await self._otel_mappings(scope, at)
            out |= await self._swarm_mappings(scope, at)
        except SourceError as exc:
            exclusions.append(
                Exclusion(
                    code="query_failed",
                    message=f"service-to-host mapping: {exc.message}",
                )
            )
        return [
            EntityMapping(kind=k, service=svc, host=host, observed_at=windows.end_time)
            for k, svc, host in sorted(out)
        ]

    async def _otel_mappings(self, scope: Scope, at: int) -> set[tuple[MappingKind, str, str]]:
        """OTel job -> host, joined via the container id in service_instance_id."""
        s = scope_matchers(scope)
        services = await self._instant(
            f"max by (job, service_instance_id) (target_info{{{s}}})", scope, at
        )
        if not services:
            return set()

        containers = await self._instant(
            f'max by (instance, id) (container_start_time_seconds{{{s}, name!=""}})',
            scope,
            at,
        )
        host_by_container: dict[str, str] = {}
        for c in containers:
            # cAdvisor ids look like ".../docker-<64 hex>.scope".
            container_id = c.labels.get("id", "").rsplit("docker-", 1)[-1].removesuffix(".scope")
            if len(container_id) >= CONTAINER_ID_PREFIX_LENGTH:
                short_id = container_id[:CONTAINER_ID_PREFIX_LENGTH]
                host_by_container[short_id] = c.labels.get("instance", "")

        out: set[tuple[MappingKind, str, str]] = set()
        for svc in services:
            instance_id = svc.labels.get("service_instance_id", "")
            host = host_by_container.get(instance_id[:CONTAINER_ID_PREFIX_LENGTH])
            if host:
                out.add((MappingKind.OTEL_SERVICE, svc.labels.get("job", ""), host))
        return out

    async def _swarm_mappings(self, scope: Scope, at: int) -> set[tuple[MappingKind, str, str]]:
        """Swarm service -> nodes currently running one of its tasks."""
        s = scope_matchers(scope)
        tasks = await self._instant(
            f"count by (service_name, node_hostname) (docker_swarm_task_info{{{s}, "
            f'state="running"}})',
            scope,
            at,
        )
        return {
            (MappingKind.SWARM_SERVICE, t.labels.get("service_name", ""), t.labels["node_hostname"])
            for t in tasks
            if t.labels.get("node_hostname")
        }


def failed_preview(
    query: str | None, start: datetime, step: int, message: str
) -> CustomRulePreview:
    return CustomRulePreview(
        ok=False,
        query=query,
        start=start,
        step_seconds=step,
        series_count=0,
        series=[],
        message=message,
    )


def preview_from_grids(
    defn: SignalDef,
    scope: Scope,
    query: str,
    start: datetime,
    step: int,
    grids: Sequence[tuple[dict[str, str], list[float | None]]],
) -> CustomRulePreview:
    series = []
    for labels, values in grids[:PREVIEW_MAX_SERIES]:
        observed = [v for v in values if v is not None]
        series.append(
            CustomRulePreviewSeries(
                entity=entity_for(defn, labels, scope),
                values=values,
                latest=observed[-1] if observed else None,
            )
        )
    if not grids:
        message: str | None = "The query returned no series in the last 24 h."
    elif len(grids) > PREVIEW_MAX_SERIES:
        message = f"Showing {PREVIEW_MAX_SERIES} of {len(grids)} series."
    else:
        message = None
    return CustomRulePreview(
        ok=True,
        query=query,
        start=start,
        step_seconds=step,
        series_count=len(grids),
        series=series,
        message=message,
    )


def collision_probes(defn: SignalDef, query: str) -> list[str]:
    """One instant query per rated counter selector of ``query``: the largest share of samples
    that reset among each entity's raw series, for entities with at least the minimum resets.

    Custom rules are skipped (their identity is not known up front), as are ``__name__``
    selectors (``resets`` drops the name, so their series would clash)."""
    if defn.entity_kind is EntityKind.CUSTOM or not defn.identity:
        return []
    by = ", ".join(defn.identity)
    probes: list[str] = []
    for metric, matchers in _RATED_SELECTOR.findall(query):
        # A histogram's count comes from the same target as its buckets, in fewer series.
        if metric.endswith("_bucket"):
            metric = metric.removesuffix("_bucket") + "_count"
        rng = f"{metric}{matchers}[{COLLISION_WINDOW}]"
        probes.append(
            f"max by ({by}) ((resets({rng}) >= {COLLISION_MIN_RESETS}) / count_over_time({rng}))"
        )
    return list(dict.fromkeys(probes))


def _identity(labels: Mapping[str, str], defn: SignalDef) -> tuple[str, ...]:
    return tuple(labels.get(k, "") for k in defn.identity)


def _drop_above(values: list[float | None], bound: float | None) -> int:
    """Turn samples above ``bound`` into gaps in place; returns how many were dropped."""
    if bound is None:
        return 0
    invalid = [i for i, v in enumerate(values) if v is not None and v > bound]
    for i in invalid:
        values[i] = None
    return len(invalid)


@dataclass
class _Collisions:
    """Counter collision probes of one collection, and the entities they dropped."""

    probes: dict[str, frozenset[tuple[str, ...]]] = field(default_factory=dict)
    dropped: dict[str, tuple[Entity, SignalFamily, list[str]]] = field(default_factory=dict)

    def exclusions(self) -> list[Exclusion]:
        return [
            Exclusion(
                code="counter_collision",
                family=family,
                message=f"{entity.display_name}: counters reset in at least "
                f"{COLLISION_RESET_RATIO:.0%} of their samples over the latest 24 h, so several "
                "targets probably share one label set (e.g. a fixed instance label); not "
                f"analysed: {', '.join(signals)}.",
            )
            for entity, family, signals in self.dropped.values()
        ]


@dataclass(frozen=True)
class _Grid:
    """The regular collection grid: ``size`` steps of ``step`` seconds from ``start``."""

    start: datetime
    step: int
    size: int


def _collection_plan(
    capabilities: Sequence[MetricCapability], catalog: Sequence[SignalDef] = CATALOG
) -> list[SignalDef]:
    """Signals to query, traffic first: traffic decides which routes are analysed individually."""
    by_signal = {d.signal: d for d in catalog}
    plan = [
        by_signal[c.signal]
        for c in capabilities
        if c.status in (CapabilityStatus.SUPPORTED, CapabilityStatus.PARTIAL)
        and c.signal in by_signal
        and not _is_mean_latency_fallback(c)
    ]
    order = {d.signal: i for i, d in enumerate(catalog)}
    plan.sort(key=lambda d: (d.signal not in TRAFFIC_SIGNALS, order[d.signal]))
    return plan


def _is_mean_latency_fallback(capability: MetricCapability) -> bool:
    """Mean latency is collected only when no percentile is available."""
    return bool(
        capability.signal == MEAN_LATENCY_SIGNAL
        and capability.reason
        and capability.reason.startswith(_FALLBACK_REASON_PREFIX)
    )


def _mark_mean_latency_fallback(caps: list[MetricCapability]) -> list[MetricCapability]:
    """Demote supported mean latency to a fallback when an HTTP percentile is supported."""
    percentile_supported = any(
        c.signal.startswith("http")
        and c.signal.endswith(("_p95", "_p99"))
        and c.family is SignalFamily.LATENCY
        and c.status is CapabilityStatus.SUPPORTED
        for c in caps
    )
    if not percentile_supported:
        return caps
    return [
        c.model_copy(update={"reason": _MEAN_LATENCY_FALLBACK_REASON})
        if c.signal == MEAN_LATENCY_SIGNAL and c.status is CapabilityStatus.SUPPORTED
        else c
        for c in caps
    ]


def _to_grid(result: RangeResult, grid_start: int, step: int, n: int) -> list[float | None]:
    """Place samples on the grid; a sample evaluated at t covers [t - step, t)."""
    values: list[float | None] = [None] * n
    for ts, v in result.samples:
        i = (ts - step - grid_start) // step
        if 0 <= i < n and (ts - grid_start) % step == 0:
            values[i] = v
    return values
