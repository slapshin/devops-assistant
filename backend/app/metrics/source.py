"""PrometheusMetricsSource: the read-only, scope-enforcing MetricsSource (T004)."""

import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.common import Entity, EntityKind, Scope, SignalFamily
from app.domain.ids import series_id
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    DiscoveredValues,
    EntityMapping,
    MappingKind,
    ProgressReporter,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import AnalysisWindows, Exclusion, SourceInfo
from app.metrics.catalog import (
    ALL_REQUIRED_METRICS,
    BY_SIGNAL,
    CATALOG,
    CATALOG_VERSION,
    SignalDef,
)
from app.metrics.client import PrometheusClient, RangeResult, SourceError, SourceErrorKind
from app.metrics.promql import assert_scoped, escape_label_value, scope_matchers

log = logging.getLogger("app.metrics")

HISTORY_DAYS = 28
OTHER_ROUTES = "(other routes)"


@dataclass(frozen=True)
class CollectionBudget:
    max_series_per_query: int = 500
    max_series_per_job: int = 5000
    top_routes_per_service: int = 20
    max_projects: int = 200
    max_envs: int = 50


def entity_for(defn: SignalDef, labels: dict[str, str]) -> Entity:
    ident = {k: labels.get(k, "") for k in defn.identity}
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
        case EntityKind.ROUTE if "rpc_method" in ident:
            name = f"{ident['job']} · {ident['rpc_method']}"
        case _:
            name = f"{ident['job']} · {ident['http_request_method']} {ident['http_route']}".strip()
    return Entity(kind=defn.entity_kind, key=key, display_name=name, labels=ident)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


class PrometheusMetricsSource:
    def __init__(
        self,
        client: PrometheusClient,
        budget: CollectionBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.budget = budget or CollectionBudget()
        self._now = now or (lambda: datetime.now(UTC))

    # --- discovery ------------------------------------------------------------------------

    async def source_info(self) -> SourceInfo:
        version = None
        with contextlib.suppress(SourceError):
            version = (await self.client.buildinfo()).get("version")
        return SourceInfo(base_url=self.client.base_url, backend=None, version=version)

    async def _label_values(self, label: str, match: str, limit: int) -> DiscoveredValues:
        end = _ts(self._now())
        values = await self.client.label_values(
            label, [match], end - HISTORY_DAYS * 86400, end, limit + 1
        )
        return DiscoveredValues(values=values[:limit], truncated=len(values) > limit)

    async def list_projects(self) -> DiscoveredValues:
        return await self._label_values("project", '{project!=""}', self.budget.max_projects)

    async def list_envs(self, project: str) -> DiscoveredValues:
        match = f'{{project="{escape_label_value(project)}", env!=""}}'
        return await self._label_values("env", match, self.budget.max_envs)

    async def _instant(self, query: str, scope: Scope, at: int) -> list[RangeResult]:
        assert_scoped(query, scope, ())
        return await self.client.query(query, at)

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        at = _ts(windows.end_time)
        s = scope_matchers(scope)
        names_regex = "|".join(ALL_REQUIRED_METRICS)
        present = {
            r.labels.get("__name__", "")
            for r in await self._instant(
                f'count by (__name__) ({{__name__=~"{names_regex}", {s}}})', scope, at
            )
        }
        history_days = await self._history_days(scope, at)
        gate_cache: dict[str, bool] = {}
        caps: list[MetricCapability] = []
        percentile_ok: dict[SignalFamily, bool] = {}
        for defn in CATALOG:
            cap = await self._capability(defn, scope, at, present, history_days, gate_cache)
            caps.append(cap)
            if defn.signal.endswith(("_p95", "_p99")) and defn.signal.startswith("http"):
                percentile_ok[defn.family] = percentile_ok.get(defn.family, False) or (
                    cap.status is CapabilityStatus.SUPPORTED
                )
        return [
            c.model_copy(update={"reason": "Fallback only; percentiles are available."})
            if c.signal == "http_latency_mean"
            and percentile_ok.get(SignalFamily.LATENCY)
            and c.status is CapabilityStatus.SUPPORTED
            else c
            for c in caps
        ]

    async def _history_days(self, scope: Scope, at: int) -> float | None:
        query = f"count(up{{{scope_matchers(scope)}}})"
        assert_scoped(query, scope, ("up",))
        res = await self.client.query_range(query, at - 30 * 86400, at, 3600)
        stamps = [ts for r in res for ts, v in r.samples if v]
        return round((at - min(stamps)) / 86400, 2) if stamps else None

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
            "required_labels": ["project", "env", *defn.identity],
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
        n = int((windows.end_time - grid_start).total_seconds()) // step
        by_signal = {c.signal: c for c in capabilities}
        plan = [
            BY_SIGNAL[c.signal]
            for c in capabilities
            if c.status in (CapabilityStatus.SUPPORTED, CapabilityStatus.PARTIAL)
            and c.signal in BY_SIGNAL
            and not (
                c.signal == "http_latency_mean" and c.reason and c.reason.startswith("Fallback")
            )
        ]
        # Traffic signals first: they decide which routes are analysed individually.
        plan.sort(
            key=lambda d: (d.signal not in ("http_requests", "rpc_requests"), CATALOG.index(d))
        )
        exclusions: list[Exclusion] = []
        series: list[MetricSeries] = []
        top_routes: dict[str, set[tuple[str, ...]]] = {}
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
            query = defn.query.render(scope)
            try:
                results = await self.client.query_range(
                    query, _ts(grid_start) + step, _ts(windows.end_time), step
                )
            except SourceError as exc:
                code = "query_timeout" if exc.kind is SourceErrorKind.TIMEOUT else "query_failed"
                exclusions.append(
                    Exclusion(
                        code=code,
                        family=defn.family,
                        message=f"{defn.signal}: {exc.message}",
                    )
                )
                continue
            results = self._scoped_results(results, scope, defn, exclusions)
            if len(results) > self.budget.max_series_per_query:
                exclusions.append(
                    Exclusion(
                        code="series_truncated",
                        family=defn.family,
                        message=f"{defn.signal}: {len(results)} series exceed the per-query budget "
                        f"({self.budget.max_series_per_query}); kept the first "
                        f"{self.budget.max_series_per_query} by identity.",
                    )
                )
                results = results[: self.budget.max_series_per_query]
            grids = [(dict(r.labels), _to_grid(r, _ts(grid_start), step, n)) for r in results]
            if defn.route_level:
                grids = self._limit_routes(defn, grids, top_routes, n)
            for labels, values in grids:
                entity = entity_for(defn, dict(labels))
                coverage = sum(v is not None for v in values) / n
                series.append(
                    MetricSeries(
                        series_id=series_id(query, entity.key),
                        family=defn.family,
                        signal=defn.signal,
                        entity=entity,
                        labels={"project": scope.project, "env": scope.env, **entity.labels},
                        unit=defn.unit,
                        query=query,
                        step_seconds=step,
                        start=grid_start,
                        values=values,
                        coverage=coverage,
                    )
                )
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
        mappings = await self._mappings(scope, windows, by_signal, exclusions)
        log.info(
            "collected scope=%s/%s catalog=%s series=%d exclusions=%d requests=%d",
            scope.project,
            scope.env,
            CATALOG_VERSION,
            len(series),
            len(exclusions),
            self.client.request_count,
        )
        return CollectionResult(series=series, exclusions=exclusions, mappings=mappings)

    @staticmethod
    def _scoped_results(
        results: list[RangeResult], scope: Scope, defn: SignalDef, exclusions: list[Exclusion]
    ) -> list[RangeResult]:
        """Defence in depth: drop any returned series whose labels leave the scope."""
        kept = [
            r
            for r in results
            if r.labels.get("project", scope.project) == scope.project
            and r.labels.get("env", scope.env) == scope.env
        ]
        if len(kept) != len(results):
            exclusions.append(
                Exclusion(
                    code="query_failed",
                    family=defn.family,
                    message=f"{defn.signal}: dropped {len(results) - len(kept)} foreign series",
                )
            )
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
        traffic = "rpc_requests" if "rpc_method" in defn.identity else "http_requests"
        recent = n // 2
        if defn.signal == traffic:
            per_job: dict[str, list[tuple[float, tuple[str, ...]]]] = defaultdict(list)
            for labels, values in grids:
                lab = labels
                volume = sum(v for v in values[recent:] if v is not None)
                per_job[lab.get("job", "")].append(
                    (volume, tuple(lab.get(k, "") for k in route_labels))
                )
            for job, items in per_job.items():
                items.sort(key=lambda x: (-x[0], x[1]))
                top_routes[f"{traffic}|{job}"] = {
                    r for _, r in items[: self.budget.top_routes_per_service]
                }
        kept: list[tuple[dict[str, str], list[float | None]]] = []
        other: dict[str, list[float | None]] = {}
        for labels, values in grids:
            lab = labels
            job = lab.get("job", "")
            allowed = top_routes.get(f"{traffic}|{job}")
            route = tuple(lab.get(k, "") for k in route_labels)
            if allowed is None or route in allowed:
                kept.append((labels, values))
            elif defn.unit.value.endswith("per_second"):
                acc = other.setdefault(job, [None] * n)
                for i, v in enumerate(values):
                    if v is not None:
                        acc[i] = (acc[i] or 0.0) + v
        for job, values in sorted(other.items()):
            labels = {
                "job": job,
                **{k: OTHER_ROUTES if k != "http_request_method" else "" for k in route_labels},
            }
            kept.append((labels, values))
        return kept

    async def _mappings(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        by_signal: dict[str, MetricCapability],
        exclusions: list[Exclusion],
    ) -> list[EntityMapping]:
        at = _ts(windows.end_time)
        s = scope_matchers(scope)
        out: set[tuple[MappingKind, str, str]] = set()
        try:
            services = await self._instant(
                f"max by (job, service_instance_id) (target_info{{{s}}})", scope, at
            )
            if services:
                containers = await self._instant(
                    f'max by (instance, id) (container_start_time_seconds{{{s}, name!=""}})',
                    scope,
                    at,
                )
                by_prefix: dict[str, str] = {}
                for c in containers:
                    cid = c.labels.get("id", "").rsplit("docker-", 1)[-1].removesuffix(".scope")
                    if len(cid) >= 12:
                        by_prefix[cid[:12]] = c.labels.get("instance", "")
                for svc in services:
                    host = by_prefix.get(svc.labels.get("service_instance_id", "")[:12])
                    if host:
                        out.add((MappingKind.OTEL_SERVICE, svc.labels.get("job", ""), host))
            tasks = await self._instant(
                f"count by (service_name, node_hostname) (docker_swarm_task_info{{{s}, "
                f'state="running"}})',
                scope,
                at,
            )
            for t in tasks:
                if t.labels.get("node_hostname"):
                    out.add(
                        (
                            MappingKind.SWARM_SERVICE,
                            t.labels.get("service_name", ""),
                            t.labels["node_hostname"],
                        )
                    )
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


def _to_grid(result: RangeResult, grid_start: int, step: int, n: int) -> list[float | None]:
    """Place samples on the grid; a sample evaluated at t covers [t - step, t)."""
    values: list[float | None] = [None] * n
    for ts, v in result.samples:
        i = (ts - step - grid_start) // step
        if 0 <= i < n and (ts - grid_start) % step == 0:
            values[i] = v
    return values
