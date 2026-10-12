"""GrafanaMetricsSource: state history of selected Grafana alert rules as series (T019).

Each selected rule with at least one matching instance is its own entity. Its history is read
per rule over the whole window and replayed per instance: the state before the first change is
that change's previous state; an instance without changes keeps its current state (from the
rules API) for the whole window. Per 5-minute bucket, a rule's value is the number of matching
instances that were firing (or could not evaluate) at any time in the bucket. Buckets where
every instance was paused, or that lie before the oldest change of a truncated history, are
unknown (None), so a paused rule or missing history is never reported as quiet.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from app.domain.common import Entity, EntityKind, LabelMatcher, Scope, SourceKind
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
from app.sources.grafana.api import SECONDS_PER_DAY, AlertRule, GrafanaApi, History, Transition
from app.sources.grafana.catalog import (
    CATALOG_VERSION,
    FOLDER_ATTRIBUTE,
    GROUP_ATTRIBUTE,
    INSTANCES_ATTRIBUTE,
    MAX_ATTRIBUTE_INSTANCES,
    MAX_LISTED,
    MAX_RULES,
    MAX_TRANSITIONS_PER_RULE,
    SEVERITY_LABEL,
    SIGNALS,
    GrafanaSignal,
    State,
    classify,
    display_query,
    instance_matches,
    instance_summary,
    label_text,
    selects,
    signals_of,
)

log = logging.getLogger("app.sources.grafana")
UNLABELLED = "{}"
"""Instance key of history entries without a label set (only counted without instance matchers)."""

Mask = NDArray[np.bool_]


def rule_entity(rule: AlertRule) -> Entity:
    return Entity(
        kind=EntityKind.ALERT_RULE,
        key=f"alert_rule|uid={rule.uid}",
        display_name=rule.title,
        labels={"rule_uid": rule.uid},
    )


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


@dataclass(frozen=True)
class Selection:
    rules: list[AlertRule]
    """Selected rules in folder and title order, at most ``MAX_RULES``."""
    truncated: bool = False


@dataclass
class _Instance:
    labels: dict[str, str]
    changes: list[Transition] = field(default_factory=list)
    state: str | None = None
    """Current state (rules API), when the instance still exists."""
    active_at: int | None = None


@dataclass
class _RuleSeries:
    """Per-bucket counts of one rule and what was dropped while building them."""

    firing: list[float | None]
    nodata: list[float | None]
    recent_firing: list[str]
    """Summaries of instances firing in the latest 24 h."""
    unlabelled: int = 0


class GrafanaMetricsSource:
    def __init__(
        self,
        api: GrafanaApi,
        folders: Sequence[str] = (),
        rule_uids: Sequence[str] = (),
        rule_labels: Sequence[LabelMatcher] = (),
        instance_labels: Sequence[LabelMatcher] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.folders = list(folders)
        self.rule_uids = list(rule_uids)
        self.rule_labels = list(rule_labels)
        self.instance_labels = list(instance_labels)
        self._now = now or (lambda: datetime.now(UTC))
        self._selection: Selection | None = None

    async def source_info(self) -> SourceInfo:
        return SourceInfo(
            base_url=self.api.base_url, backend=self.api.backend, version=await self.api.version()
        )

    # --- selection ------------------------------------------------------------------------

    async def selection(self) -> Selection:
        """The configured rules, read once per source (one analysis or probe)."""
        if self._selection is None:
            rules = [
                r
                for r in await self.api.rules()
                if selects(r, self.folders, self.rule_uids, self.rule_labels)
            ]
            rules.sort(key=lambda r: (r.folder, r.title, r.uid))
            self._selection = Selection(rules[:MAX_RULES], truncated=len(rules) > MAX_RULES)
        return self._selection

    def matches(self, labels: Mapping[str, str]) -> bool:
        """Whether an alert instance with these labels is counted."""
        return instance_matches(labels, self.instance_labels)

    def matching_instances(self, rule: AlertRule) -> int:
        """Current instances of a rule that match the instance labels."""
        return sum(self.matches(i.labels) for i in rule.instances)

    @staticmethod
    def _window(windows: AnalysisWindows) -> tuple[int, int]:
        end = _ts(windows.end_time)
        return _ts(windows.end_time - timedelta(days=HISTORY_DAYS)), end

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        self.api.begin(windows.end_time)
        selection = await self.selection()
        used = {s.signal for rule in selection.rules for s in signals_of(rule)}
        if not selection.rules:
            reason = (
                "No Grafana-managed alert rule matches the configured folders, rule UIDs and "
                "rule labels."
            )
            return [self._capability(s, CapabilityStatus.UNSUPPORTED, reason) for s in SIGNALS]
        return [
            self._capability(s, CapabilityStatus.SUPPORTED, None)
            for s in SIGNALS
            if s.signal in used
        ]

    @staticmethod
    def _capability(
        defn: GrafanaSignal, status: CapabilityStatus, reason: str | None
    ) -> MetricCapability:
        metric = f"grafana:{defn.signal}"
        supported = status is CapabilityStatus.SUPPORTED
        return MetricCapability(
            source=SourceKind.GRAFANA,
            family=defn.family,
            signal=defn.signal,
            status=status,
            verified=True,
            required_metrics=[metric],
            required_labels=[],
            observed_metrics=[metric] if supported else [],
            reason=reason,
        )

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
        supported = {c.signal for c in capabilities if c.status is CapabilityStatus.SUPPORTED}
        selection = await self.selection()
        rules = [r for r in selection.rules if signals_of(r)[0].signal in supported]

        total = len(rules)
        done = 0
        started = self._now()

        async def fetch(rule: AlertRule) -> History | SourceError:
            nonlocal done
            cancel.raise_if_cancelled()
            try:
                return await self.api.history(rule, grid_start, end)
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
                        message="grafana alert history",
                    )
                )

        results = await asyncio.gather(*(fetch(rule) for rule in rules))
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
        failures: list[tuple[AlertRule, SourceError]] = []
        truncated: list[AlertRule] = []
        unmatched: list[AlertRule] = []
        unlabelled = 0
        for rule, result in zip(rules, results, strict=True):
            if isinstance(result, SourceError):
                failures.append((rule, result))
                continue
            if result.truncated:
                truncated.append(rule)
            built = self._rule_series(rule, result, grid_start, end, step)
            if built is None:
                unmatched.append(rule)
                continue
            unlabelled += built.unlabelled
            series += self._metric_series(rule, built, grid_start, step, supported)

        exclusions = _failure_exclusions(failures, len(rules))
        exclusions += self._selection_exclusions(selection, truncated, unmatched, unlabelled)
        log.info(
            "collected project=%s grafana rules=%d catalog=%s series=%d exclusions=%d",
            scope.project_id,
            len(rules),
            CATALOG_VERSION,
            len(series),
            len(exclusions),
        )
        return CollectionResult(series=series, exclusions=exclusions)

    def _instances(self, rule: AlertRule, history: History) -> tuple[dict[str, _Instance], int]:
        """Matching instances by label text, and the count of unattributable changes."""
        instances: dict[str, _Instance] = {}
        unlabelled = 0
        for change in history.transitions:
            key = change.instance
            if key is None:
                if self.instance_labels:
                    unlabelled += 1
                    continue
                key = UNLABELLED
            if key not in instances:
                if not instance_matches(change.labels, self.instance_labels):
                    continue
                instances[key] = _Instance(change.labels)
            instances[key].changes.append(change)
        for current in rule.instances:
            if not instance_matches(current.labels, self.instance_labels):
                continue
            found = instances.setdefault(label_text(current.labels), _Instance(current.labels))
            found.state, found.active_at = current.state, current.active_at
        return instances, unlabelled

    def _rule_series(
        self, rule: AlertRule, history: History, grid_start: int, end: int, step: int
    ) -> _RuleSeries | None:
        instances, unlabelled = self._instances(rule, history)
        if not instances:
            return None
        size = (end - grid_start) // step
        firing = np.zeros(size)
        nodata = np.zeros(size)
        known = np.zeros(size, dtype=bool)
        recent: list[str] = []
        day_start = size - SECONDS_PER_DAY // step
        hidden = {m.name for m in self.instance_labels}
        for instance in instances.values():
            fire, missing, seen = _replay(instance, rule, history, grid_start, end, step)
            firing += fire
            nodata += missing
            known |= seen
            if fire[day_start:].any():
                recent.append(instance_summary(instance.labels, hidden))

        def values(counts: NDArray[np.float64]) -> list[float | None]:
            return [float(c) if k else None for c, k in zip(counts, known, strict=True)]

        return _RuleSeries(values(firing), values(nodata), sorted(recent), unlabelled)

    def _metric_series(
        self,
        rule: AlertRule,
        built: _RuleSeries,
        grid_start: int,
        step: int,
        supported: set[str],
    ) -> list[MetricSeries]:
        entity = rule_entity(rule)
        labels = dict(entity.labels) | {FOLDER_ATTRIBUTE: rule.folder, GROUP_ATTRIBUTE: rule.group}
        if severity := rule.labels.get(SEVERITY_LABEL):
            labels[SEVERITY_LABEL] = severity
        if built.recent_firing:
            shown = built.recent_firing[:MAX_ATTRIBUTE_INSTANCES]
            more = len(built.recent_firing) - len(shown)
            labels[INSTANCES_ATTRIBUTE] = "; ".join(shown) + (f"; and {more} more" if more else "")
        out = []
        for defn in signals_of(rule):
            if defn.signal not in supported:
                continue
            values = built.firing if defn.state is State.FIRING else built.nodata
            query = display_query(rule, defn, self.instance_labels)
            observed = sum(v is not None for v in values)
            out.append(
                MetricSeries(
                    series_id=series_id(f"{defn.signal}\n{query}", entity.key),
                    family=defn.family,
                    signal=defn.signal,
                    entity=entity,
                    labels=labels,
                    unit=defn.unit,
                    query=query,
                    step_seconds=step,
                    start=datetime.fromtimestamp(grid_start, UTC),
                    values=values,
                    coverage=observed / len(values) if values else 0.0,
                )
            )
        return out

    def _selection_exclusions(
        self,
        selection: Selection,
        truncated: list[AlertRule],
        unmatched: list[AlertRule],
        unlabelled: int,
    ) -> list[Exclusion]:
        out = []
        if selection.truncated:
            out.append(
                Exclusion(
                    code="series_truncated",
                    message=f"More than {MAX_RULES} Grafana alert rules match the selection; only "
                    f"the first {MAX_RULES} by folder and title are analysed. Narrow the folders "
                    "or rule labels.",
                )
            )
        if truncated:
            out.append(
                Exclusion(
                    code="series_truncated",
                    message=f"Grafana alert rule(s) {_names(truncated)} changed state more than "
                    f"{MAX_TRANSITIONS_PER_RULE:,} times in the window; their oldest history is "
                    "unknown.",
                )
            )
        if unlabelled:
            out.append(
                Exclusion(
                    code="no_data",
                    message=f"{unlabelled:,} Grafana state changes have no readable instance "
                    "labels, so the instance label matchers cannot apply; they are not counted.",
                )
            )
        if unmatched:
            selection_text = ", ".join(f"{m.name}={m.value}" for m in self.instance_labels)
            message = (
                f"Grafana alert rule(s) {_names(unmatched)} have no instance matching "
                f"{selection_text} in the last {HISTORY_DAYS} days; they are not analysed."
                if self.instance_labels
                else f"Grafana alert rule(s) {_names(unmatched)} have no instances in the last "
                f"{HISTORY_DAYS} days (never evaluated); they are not analysed."
            )
            out.append(Exclusion(code="no_data", message=message))
        return out


def _replay(
    instance: _Instance,
    rule: AlertRule,
    history: History,
    grid_start: int,
    end: int,
    step: int,
) -> tuple[Mask, Mask, Mask]:
    """Per bucket: firing at any time, unable to evaluate at any time, and state known."""
    size = (end - grid_start) // step
    fire = np.zeros(size, dtype=bool)
    missing = np.zeros(size, dtype=bool)
    seen = np.zeros(size, dtype=bool)
    for lo_t, hi_t, state in _segments(instance, rule, history, grid_start, end):
        if state is None or state is State.PAUSED or hi_t <= lo_t:
            continue
        lo = max(0, (lo_t - grid_start) // step)
        hi = min(size, -((grid_start - hi_t) // step))
        seen[lo:hi] = True
        if state is State.FIRING:
            fire[lo:hi] = True
        elif state is State.NODATA:
            missing[lo:hi] = True
    return fire, missing, seen


def _segments(
    instance: _Instance, rule: AlertRule, history: History, grid_start: int, end: int
) -> list[tuple[int, int, State | None]]:
    """(start, end, state) intervals covering [grid_start, end); None is unknown."""
    changes = instance.changes
    start = max(grid_start, min(history.known_from or grid_start, end))
    out: list[tuple[int, int, State | None]] = [(grid_start, start, None)]
    if not changes:
        if instance.state is None:
            state = State.PAUSED if rule.paused else State.NORMAL
        else:
            state = classify(instance.state)
            active_at = instance.active_at
            if state is not State.NORMAL and active_at is not None and active_at >= end:
                state = State.NORMAL  # became active after the analysed window
        return [*out, (start, end, state)]
    out.append((start, changes[0].time, classify(changes[0].prev)))
    for change, following in zip(changes, [*changes[1:], None], strict=True):
        stop = following.time if following is not None else end
        out.append((change.time, stop, classify(change.next)))
    return out


def _names(rules: Sequence[AlertRule]) -> str:
    shown = ", ".join(f'"{r.title}"' for r in rules[:MAX_LISTED])
    more = f" and {len(rules) - MAX_LISTED} more" if len(rules) > MAX_LISTED else ""
    return shown + more


def _failure_exclusions(
    failures: list[tuple[AlertRule, SourceError]], planned: int
) -> list[Exclusion]:
    if not failures:
        return []
    first = failures[0][1]
    code = "query_timeout" if first.kind is SourceErrorKind.TIMEOUT else "query_failed"
    rules = [rule for rule, _ in failures]
    return [
        Exclusion(
            code=code,
            family=SIGNALS[0].family,
            message=f"Grafana alert history: {len(failures)} of {planned} rules failed "
            f"({first.kind.value}: {first.message}); {_names(rules)} are unknown.",
        )
    ]
