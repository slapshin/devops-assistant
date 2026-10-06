"""WazuhMetricsSource: alerts of selected Wazuh agents as metric series (T018).

Each agent with alerts in the collected window is its own entity. The selection is the named
agents plus the current members of the configured groups (from the monitoring index), narrowed
by the agent label filters; agents are found by one discovery search over the whole window,
capped at ``MAX_AGENTS`` (more are reported as truncated). The window is then fetched in
chunks sized to the aggregation bucket budget, all agents per request. Inside a fetched chunk a
bucket without alerts is zero, from the agent's first alert on; before it and outside fetched
chunks values stay unknown (None), so a new agent or a failed request is never reported as a
quiet host.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
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
from app.domain.projects import WazuhLabel
from app.domain.report import AnalysisWindows, Exclusion, SourceInfo
from app.sources.base import HISTORY_DAYS
from app.sources.wazuh.api import (
    SECONDS_PER_DAY,
    AgentInfo,
    AgentList,
    Chunk,
    GroupMembers,
    WazuhApi,
)
from app.sources.wazuh.catalog import (
    BY_SIGNAL,
    CATALOG_VERSION,
    MAX_AGENTS,
    MAX_GROUP_MEMBERS,
    MEMBERSHIP_SECONDS,
    SIGNALS,
    WazuhSignal,
    chunk_seconds,
    display_query,
)

log = logging.getLogger("app.sources.wazuh")
MAX_LISTED = 10
"""Agent names listed in one exclusion message."""


def agent_entity(name: str) -> Entity:
    return Entity(
        kind=EntityKind.AGENT, key=f"agent|name={name}", display_name=name, labels={"agent": name}
    )


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


class WazuhMetricsSource:
    def __init__(
        self,
        api: WazuhApi,
        index_pattern: str,
        agents: Sequence[str] = (),
        labels: Sequence[WazuhLabel] = (),
        groups: Sequence[str] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.index_pattern = index_pattern
        self.agents = list(agents)
        self.labels = [(label.key, label.value) for label in labels]
        self.groups = list(groups)
        self._now = now or (lambda: datetime.now(UTC))
        self._found: tuple[tuple[int, int], AgentList] | None = None
        self._members: tuple[int, GroupMembers] | None = None

    async def source_info(self) -> SourceInfo:
        return SourceInfo(
            base_url=self.api.base_url, backend=self.api.backend, version=await self.api.version()
        )

    # --- discovery ------------------------------------------------------------------------

    async def members(self, end: int) -> GroupMembers | None:
        """Agents of the configured groups in the 24 h before ``end``; None without groups."""
        if not self.groups:
            return None
        if self._members is None or self._members[0] != end:
            members = await self.api.group_members(self.groups, end - MEMBERSHIP_SECONDS, end)
            self._members = (end, members)
        return self._members[1]

    async def selection(self, end: int) -> list[str] | None:
        """Agent names to analyse; None selects by label filters alone."""
        members = await self.members(end)
        if members is None:
            return self.agents or None
        return sorted(set(self.agents) | set(members.names()))

    async def discover(self, start: int, end: int) -> AgentList:
        """Selected agents with alerts in [start, end), cached for the same window."""
        if self._found is None or self._found[0] != (start, end):
            found = await self.api.agents(await self.selection(end), start, end)
            self._found = ((start, end), found)
        return self._found[1]

    @staticmethod
    def _window(windows: AnalysisWindows) -> tuple[int, int]:
        end = _ts(windows.end_time)
        return _ts(windows.end_time - timedelta(days=HISTORY_DAYS)), end

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        self.api.begin(windows.end_time)
        start, end = self._window(windows)
        found = await self.discover(start, end)
        supported = bool(found.agents)
        history = None
        if supported:
            first = min(a.first_seen for a in found.agents)
            history = round(max(0.0, (end - first) / SECONDS_PER_DAY), 2)
        reason = None
        if not supported:
            reason = (
                f"No alerts from the selected agents in the last {HISTORY_DAYS} days; check "
                "the agent names, groups, labels and index pattern."
            )
        return [
            MetricCapability(
                source=SourceKind.WAZUH,
                family=defn.family,
                signal=defn.signal,
                status=CapabilityStatus.SUPPORTED if supported else CapabilityStatus.UNSUPPORTED,
                verified=True,
                required_metrics=[f"{self.index_pattern}:{defn.signal}"],
                required_labels=[],
                observed_metrics=[f"{self.index_pattern}:{defn.signal}"] if supported else [],
                reason=reason,
                history_days=history,
            )
            for defn in SIGNALS
        ]

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
        grid_start, end = self._window(windows)
        size = (end - grid_start) // step
        found = await self.discover(grid_start, end)
        signals = [
            BY_SIGNAL[c.signal]
            for c in capabilities
            if c.signal in BY_SIGNAL and c.status is CapabilityStatus.SUPPORTED
        ]
        agents = found.agents if signals else []
        names = [a.name for a in agents]
        width = chunk_seconds(len(names), step)
        plan = [(s, min(s + width, end)) for s in range(grid_start, end, width)] if names else []

        total = len(plan)
        done = 0
        started = self._now()

        async def fetch(lo: int, hi: int) -> Chunk | SourceError:
            nonlocal done
            cancel.raise_if_cancelled()
            try:
                return await self.api.series(names, lo, hi)
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
                        message="wazuh alerts",
                    )
                )

        results = await asyncio.gather(*(fetch(lo, hi) for lo, hi in plan))
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

        fetched = [
            (lo, hi, r) for (lo, hi), r in zip(plan, results, strict=True) if isinstance(r, Chunk)
        ]
        failures = [r for r in results if isinstance(r, SourceError)]
        series = [
            self._series(defn, agent, fetched, grid_start, size, step, width)
            for agent in agents
            for defn in signals
        ]
        exclusions = _failure_exclusions(failures, len(plan), signals)
        exclusions += self._selection_exclusions(found, await self.members(end))

        log.info(
            "collected project=%s wazuh agents=%d catalog=%s series=%d exclusions=%d requests=%d",
            scope.project_id,
            len(agents),
            CATALOG_VERSION,
            len(series),
            len(exclusions),
            total,
        )
        return CollectionResult(series=series, exclusions=exclusions)

    def _series(
        self,
        defn: WazuhSignal,
        agent: AgentInfo,
        fetched: list[tuple[int, int, Chunk]],
        grid_start: int,
        size: int,
        step: int,
        width: int,
    ) -> MetricSeries:
        values: list[float | None] = [None] * size
        first = max(0, (agent.first_seen - grid_start) // step)
        for chunk_start, chunk_end, chunk in fetched:
            lo = max(first, (chunk_start - grid_start) // step)
            hi = min(size, (chunk_end - grid_start) // step)
            if hi <= lo:
                continue
            values[lo:hi] = [0.0] * (hi - lo)
            for bucket, count in chunk.values.get(defn.signal, {}).get(agent.name, {}).items():
                i = (bucket - grid_start) // step
                if lo <= i < hi:
                    values[i] = round(count / step, 6)

        entity = agent_entity(agent.name)
        query = display_query(self.index_pattern, defn, agent.name, self.labels, width)
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

    def _selection_exclusions(
        self, found: AgentList, members: GroupMembers | None
    ) -> list[Exclusion]:
        out = []
        if members is not None and members.truncated:
            out.append(
                Exclusion(
                    code="series_truncated",
                    message=f"A Wazuh group has more than {MAX_GROUP_MEMBERS} agents; only the "
                    f"first {MAX_GROUP_MEMBERS} by name are considered.",
                )
            )
        if members is not None and (empty := [g for g, n in members.members.items() if not n]):
            out.append(
                Exclusion(
                    code="no_data",
                    message=f"Wazuh group(s) {', '.join(empty)} have no agents in the monitoring "
                    "snapshots of the last 24 h (wrong name, or no agents assigned).",
                )
            )
        if found.truncated:
            out.append(
                Exclusion(
                    code="series_truncated",
                    message=f"More than {MAX_AGENTS} Wazuh agents match the selection; only "
                    f"the first {MAX_AGENTS} by name are analysed. Narrow the agent names or "
                    "labels.",
                )
            )
        seen = {a.name for a in found.agents}
        expected = sorted(set(self.agents) | set(members.names() if members else []))
        if not found.truncated and (silent := [a for a in expected if a not in seen]):
            shown = ", ".join(silent[:MAX_LISTED])
            more = f" and {len(silent) - MAX_LISTED} more" if len(silent) > MAX_LISTED else ""
            out.append(
                Exclusion(
                    code="no_data",
                    message=f"No alerts from Wazuh agent(s) {shown}{more} in the last "
                    f"{HISTORY_DAYS} days (wrong name, not reporting, filtered out by labels, or "
                    "quiet); they are not analysed.",
                )
            )
        return out


def _failure_exclusions(
    failures: list[SourceError], planned: int, signals: Sequence[WazuhSignal]
) -> list[Exclusion]:
    if not failures:
        return []
    first = failures[0]
    code = "query_timeout" if first.kind is SourceErrorKind.TIMEOUT else "query_failed"
    return [
        Exclusion(
            code=code,
            family=family,
            message=f"Wazuh alerts: {len(failures)} of {planned} chunks failed "
            f"({first.kind.value}: {first.message}); those periods are unknown.",
        )
        for family in sorted({s.family for s in signals})
    ]
