"""RobustDetector: deterministic, AI-free implementation of the Detector interface (T005)."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from app.analysis.baseline import STEPS_PER_DAY
from app.analysis.derive import AnalysisSeries, derive, to_array
from app.analysis.detect import (
    ABS_LEVEL_CRITICAL,
    EVENT_MIN_VALUE,
    MINUTES_PER_STEP,
    SHORTFALL_MIN_VALUE,
    TREND_STEPS,
    Episode,
    SeriesEvaluation,
    evaluate,
)
from app.analysis.rules import SEVERITY_ORDER, Dir, RuleKind, format_value
from app.domain.common import (
    ConfidenceLevel,
    Entity,
    EntityKind,
    Reason,
    Severity,
    SignalFamily,
    TimeRange,
    Unit,
)
from app.domain.detector_config import DetectorConfig, SignalThresholds
from app.domain.findings import (
    DailyTrend,
    DetectionMethod,
    EpisodeSummary,
    Evidence,
    ExpectedValue,
    Finding,
    FindingState,
    ObservedValue,
    Recurrence,
    SignalCoverage,
    SignalStatus,
    TrendBucketStatus,
    TrendDirection,
    TrendSummary,
)
from app.domain.ids import episode_id, evidence_id, finding_id, series_id
from app.domain.interfaces import CollectionResult, DetectionResult, MappingKind
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import TREND_DAYS, AnalysisRequest, AnalysisWindows, Exclusion

EVIDENCE_PAD_STEPS = 72  # ± 6 h
COVERAGE_PAD_STEPS = 12  # ± 1 h
MAX_EPISODES_PER_BUCKET = 200
MAX_EXTRA_EVIDENCE = 2
LATEST_START = TREND_STEPS - STEPS_PER_DAY
MINUTES_PER_DAY = 24 * 60
MAX_REPORTED_BASELINE_DAYS = 14
"""Upper bound of DailyTrend.baseline_days_used in the report contract."""

OTEL_MAPPING_PREFIX = "otel"
SWARM_MAPPING_PREFIX = "swarm"
JOB_LABEL = "job"
HOST_ATTRIBUTE = "host"

# Upper bound of severity points per severity; anything above is critical.
SEVERITY_MAX_POINTS = ((1, Severity.LOW), (2, Severity.MEDIUM), (4, Severity.HIGH))

# Confidence reasons.
SHORT_BASELINE_DAYS = 7
LOW_COVERAGE = 0.7
REDUCED_COVERAGE = 0.9
LOW_VOLUME_REQUESTS_PER_STEP = 300
SHORT_EPISODE_STEPS = 6
LOW_CONFIDENCE_SUFFIX = "_low"

RECURRING_MIN_PRIOR_DAYS = 3
LATENCY_HISTOGRAM_TOP_SECONDS = 10.0
"""Top finite histogram bucket: p95 at or above it is only a lower bound."""

# Trend summary: last 7 buckets vs the 7 before.
RECENT_BUCKETS = range(7)
PREVIOUS_BUCKETS = range(7, 14)
MIN_OK_BUCKETS_PER_HALF = 4
HIGH_CONFIDENCE_OK_BUCKETS = 14
MEDIUM_CONFIDENCE_OK_BUCKETS = 10
TREND_CHANGE_FACTOR = 1.5
TREND_MIN_SHARE_CHANGE = 0.005

_CAPABILITY_RANK = {
    CapabilityStatus.SUPPORTED: 3,
    CapabilityStatus.PARTIAL: 2,
    CapabilityStatus.UNVERIFIED: 1,
    CapabilityStatus.UNSUPPORTED: 0,
}


def analysis_key(request: AnalysisRequest) -> str:
    return (
        f"{request.scope.project}|{request.scope.env}|{request.end_time.isoformat()}|"
        f"{request.config_hash}"
    )


def severity_from_points(points: int, cap: Severity | None) -> Severity:
    severity = next(
        (severity for max_points, severity in SEVERITY_MAX_POINTS if points <= max_points),
        Severity.CRITICAL,
    )
    if cap is not None and SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index(cap):
        return cap
    return severity


def _opt(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def _mapping_key(prefix: str, service: str) -> str:
    return f"{prefix}:{service}"


def hosts_for(entity: Entity, mappings: dict[str, set[str]]) -> set[str]:
    """Hosts an entity verifiably runs on, from identity labels or label-based mappings."""
    labels = entity.labels
    match entity.kind:
        case (
            EntityKind.NODE
            | EntityKind.FILESYSTEM
            | EntityKind.DISK
            | EntityKind.NETWORK_INTERFACE
            | EntityKind.CONTAINER
        ):
            return {labels["instance"]} if labels.get("instance") else set()
        case EntityKind.SERVICE:
            key = _mapping_key(SWARM_MAPPING_PREFIX, labels.get("service_name", ""))
            return mappings.get(key, set())
        case EntityKind.ROUTE:
            return mappings.get(_mapping_key(OTEL_MAPPING_PREFIX, labels.get(JOB_LABEL, "")), set())
    return set()


@dataclass(frozen=True)
class _TrendContext:
    """Inputs shared by every trend bucket of one detection run."""

    key: str
    at: Callable[[int], datetime]
    finding_for_episode: dict[tuple[int, int], str]
    entities: dict[str, list[tuple[int, SeriesEvaluation]]]
    """Entity key -> (evaluation index, evaluation)."""
    present: set[str]
    """Entities with at least one sample anywhere in the grid."""


@dataclass
class _BucketActivity:
    observed_minutes: int = 0
    anomalous_minutes: int = 0
    affected: set[str] = field(default_factory=set)
    summaries: list[EpisodeSummary] = field(default_factory=list)
    peak: Severity | None = None


def _bucket_bounds(bucket_index: int) -> tuple[int, int]:
    """Trend-step range [lo, hi) of a bucket (0 = latest day)."""
    lo = (TREND_DAYS - 1 - bucket_index) * STEPS_PER_DAY
    return lo, lo + STEPS_PER_DAY


class RobustDetector:
    def detect(
        self,
        request: AnalysisRequest,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        collection: CollectionResult,
        config: DetectorConfig,
    ) -> DetectionResult:
        key = analysis_key(request)
        step = windows.step_seconds
        analysed = derive(collection.series, step, config.min_ratio_requests_per_step)
        evaluations = [evaluate(s, config) for s in analysed]

        mappings: dict[str, set[str]] = defaultdict(set)
        for m in collection.mappings:
            is_otel = m.kind is MappingKind.OTEL_SERVICE
            prefix = OTEL_MAPPING_PREFIX if is_otel else SWARM_MAPPING_PREFIX
            mappings[_mapping_key(prefix, m.service)].add(m.host)

        trend_start = windows.trend.start

        def at(t: int) -> datetime:
            return trend_start + timedelta(seconds=t * step)

        # Findings are reported for latest-day episodes only; earlier ones feed the trends.
        findings: list[Finding] = []
        evidence: list[Evidence] = []
        finding_for_episode: dict[tuple[int, int], str] = {}
        for ei, ev in enumerate(evaluations):
            for ep in ev.episodes:
                if ep.end <= LATEST_START:
                    continue
                f, evs = self._finding(key, ev, ep, at, windows, config)
                findings.append(f)
                evidence.extend(evs)
                finding_for_episode[(ei, id(ep))] = f.finding_id
        findings = self._relate(findings, mappings, config)
        findings.sort(key=lambda f: (-SEVERITY_ORDER.index(f.severity), f.start, f.finding_id))

        trends = self._trends(key, evaluations, finding_for_episode, at, config)

        return DetectionResult(
            findings=findings,
            evidence=evidence,
            trends=trends,
            trend_summary=trend_summary(trends),
            coverage=self._coverage(
                capabilities, collection.exclusions, evaluations, findings, config
            ),
            exclusions=[],
        )

    # --- findings -------------------------------------------------------------------------

    def _finding(
        self,
        key: str,
        ev: SeriesEvaluation,
        ep: Episode,
        at: Callable[[int], datetime],
        windows: AnalysisWindows,
        config: DetectorConfig,
    ) -> tuple[Finding, list[Evidence]]:
        s = ev.series
        rule = s.rule
        th = config.signals.get(rule.thresholds) if rule.thresholds else None
        base = ev.baseline_for_step(ep.peak)
        start, end = at(ep.start), at(ep.end)
        fid = finding_id(key, s.entity.key, rule.name, start.isoformat())

        is_abs = ep.abs_level > 0 or rule.kind is not RuleKind.LEVEL
        method = DetectionMethod.ABSOLUTE if is_abs else DetectionMethod.RELATIVE
        threshold = _finding_threshold(ev, ep, th)
        peak_value = float(ev.values[ep.peak])
        expected = _expected_value(ev, ep, base.available, config)
        reasons = self._confidence_reasons(ev, ep, base.available, is_abs, config)
        prior = _prior_episode_days(ev, ep)

        attributes = dict(s.attributes)
        if rule.name.endswith("latency_p95") and peak_value >= LATENCY_HISTOGRAM_TOP_SECONDS:
            attributes["latency_bound"] = ">= 10 s (top histogram bucket)"

        evs = self._evidence(fid, ev, ep, windows, threshold, rule.label, config.z_threshold)
        finding = Finding(
            finding_id=fid,
            detector=f"{rule.kind.value}:{rule.name}",
            detector_version=config.version,
            method=method,
            family=rule.family,
            signal=rule.name,
            title=rule.title(up=ep.up, threshold=threshold if ep.abs_level else None, unit=s.unit),
            entity=s.entity,
            start=start,
            end=end,
            peak_at=at(ep.peak),
            duration_seconds=(ep.end - ep.start) * windows.step_seconds,
            severity=severity_from_points(ep.severity_points, rule.severity_cap),
            severity_points=ep.severity_points,
            confidence=_confidence(reasons),
            confidence_reasons=reasons,
            observed=ObservedValue(value=peak_value, unit=s.unit),
            expected=expected,
            peak_score=None if ep.peak_z is None or not base.available else round(ep.peak_z, 2),
            threshold=threshold,
            baseline_days=base.days if rule.kind is RuleKind.LEVEL else None,
            baseline_mode=base.mode,
            evidence_ids=[e.evidence_id for e in evs],
            attributes=attributes,
            state=_finding_state(ep, config),
            recurrence=_recurrence(len(prior)),
            prior_episode_days=len(prior),
        )
        if method is DetectionMethod.RELATIVE and (expected is None or finding.peak_score is None):
            raise AssertionError(  # pragma: no cover
                f"relative finding {fid} ({rule.name}) without baseline"
            )
        return finding, evs

    @staticmethod
    def _confidence_reasons(
        ev: SeriesEvaluation,
        ep: Episode,
        baseline_ok: bool,
        is_abs: bool,
        config: DetectorConfig,
    ) -> list[Reason]:
        reasons: list[Reason] = []
        base = ev.baseline_for_step(ep.peak)
        is_level = ev.series.rule.kind is RuleKind.LEVEL

        if is_level and not baseline_ok:
            min_days = config.baseline_min_adequate_days
            reasons.append(
                Reason(
                    code="baseline_low",
                    message=f"{base.days} adequate baseline days (< {min_days}); "
                    "absolute check only.",
                )
            )
        elif is_level and base.days < SHORT_BASELINE_DAYS:
            reasons.append(
                Reason(
                    code="baseline_short",
                    message=f"{base.days} adequate baseline days (< {SHORT_BASELINE_DAYS}).",
                )
            )

        lo = max(0, ep.start - COVERAGE_PAD_STEPS)
        hi = min(TREND_STEPS, ep.end + COVERAGE_PAD_STEPS)
        coverage = float(np.mean(~np.isnan(ev.values[lo:hi])))
        coverage_message = f"Coverage {coverage:.0%} in episode ± 1 h."
        if coverage < LOW_COVERAGE:
            reasons.append(Reason(code="coverage_low", message=coverage_message))
        elif coverage < REDUCED_COVERAGE:
            reasons.append(Reason(code="coverage_reduced", message=coverage_message))

        if ev.series.volume is not None:
            volume = ev.series.volume[ev.offset + ep.start : ev.offset + ep.end]
            observed_volume = volume[~np.isnan(volume)]
            if observed_volume.size and float(observed_volume.min()) < LOW_VOLUME_REQUESTS_PER_STEP:
                reasons.append(
                    Reason(
                        code="request_volume",
                        message=f"As few as {observed_volume.min():.0f} requests per 5-min step "
                        f"(< {LOW_VOLUME_REQUESTS_PER_STEP}).",
                    )
                )

        if is_level and ep.anomalous_steps < SHORT_EPISODE_STEPS and not is_abs:
            reasons.append(
                Reason(
                    code="episode_short",
                    message=f"{ep.anomalous_steps} anomalous steps (< {SHORT_EPISODE_STEPS}).",
                )
            )
        return reasons

    @staticmethod
    def _evidence(
        fid: str,
        ev: SeriesEvaluation,
        ep: Episode,
        windows: AnalysisWindows,
        threshold: float | None,
        label: str,
        z_threshold: float,
    ) -> list[Evidence]:
        lo = max(0, ep.start - EVIDENCE_PAD_STEPS)
        hi = min(TREND_STEPS, ep.end + EVIDENCE_PAD_STEPS)
        start = windows.trend.start + timedelta(seconds=lo * windows.step_seconds)

        def band(a: np.ndarray) -> list[float | None]:
            return [_opt(v) for v in a[lo:hi]]

        s = ev.series
        main = _slice_series(s, s.values[ev.offset + lo : ev.offset + hi], start, windows)
        spread = z_threshold * ev.scale
        out = [
            Evidence(
                evidence_id=evidence_id(fid, main.series_id),
                finding_id=fid,
                series=main,
                expected=band(ev.median),
                lower=band(np.maximum(0.0, ev.median - spread)),
                upper=band(ev.median + spread),
                threshold=threshold,
                threshold_label=(
                    f"{label}: diagnostic heuristic (not an SLO)" if threshold is not None else None
                ),
            )
        ]

        for extra in s.extra_evidence[:MAX_EXTRA_EVIDENCE]:
            values = to_array(extra)[ev.offset + lo : ev.offset + hi]
            extra_series = _slice_series_raw(extra, values, start)
            out.append(
                Evidence(
                    evidence_id=evidence_id(fid, extra_series.series_id),
                    finding_id=fid,
                    series=extra_series,
                )
            )
        return out

    # --- relations ------------------------------------------------------------------------

    @staticmethod
    def _relate(
        findings: list[Finding], mappings: dict[str, set[str]], config: DetectorConfig
    ) -> list[Finding]:
        hosts = {f.finding_id: hosts_for(f.entity, mappings) for f in findings}
        related = _related_finding_ids(findings, hosts, config)

        out = []
        for f in findings:
            attrs = dict(f.attributes)
            if f.entity.kind in (EntityKind.ROUTE, EntityKind.SERVICE) and hosts[f.finding_id]:
                attrs[HOST_ATTRIBUTE] = ",".join(sorted(hosts[f.finding_id]))
            out.append(
                f.model_copy(
                    update={
                        "related_finding_ids": sorted(related[f.finding_id]),
                        "attributes": attrs,
                    }
                )
            )
        return out

    # --- trends ---------------------------------------------------------------------------

    def _trends(
        self,
        key: str,
        evaluations: list[SeriesEvaluation],
        finding_for_episode: dict[tuple[int, int], str],
        at: Callable[[int], datetime],
        config: DetectorConfig,
    ) -> list[DailyTrend]:
        entities: dict[str, list[tuple[int, SeriesEvaluation]]] = defaultdict(list)
        for i, ev in enumerate(evaluations):
            entities[ev.series.entity.key].append((i, ev))
        present = {
            k
            for k, evs in entities.items()
            if any(np.any(~np.isnan(e.series.values)) for _, e in evs)
        }
        context = _TrendContext(key, at, finding_for_episode, entities, present)

        trends: list[DailyTrend] = []
        for b in range(TREND_DAYS):
            lo, hi = _bucket_bounds(b)
            activity = _bucket_activity(context, b)
            used = _median_baseline_days(evaluations, b)
            coverage = (
                activity.observed_minutes / (len(present) * MINUTES_PER_DAY) if present else 0.0
            )
            activity.summaries.sort(
                key=lambda e: (-SEVERITY_ORDER.index(e.severity), e.start, e.episode_id)
            )
            trends.append(
                DailyTrend(
                    bucket_index=b,
                    window=TimeRange(start=at(lo), end=at(hi)),
                    status=_bucket_status(coverage, used, config),
                    episode_count=len(activity.summaries),
                    anomalous_minutes=activity.anomalous_minutes,
                    peak_severity=activity.peak,
                    affected_entities=sorted(activity.affected),
                    observed_entity_minutes=activity.observed_minutes,
                    anomalous_share=(
                        round(activity.anomalous_minutes / activity.observed_minutes, 6)
                        if activity.observed_minutes
                        else None
                    ),
                    baseline_days_used=min(used, MAX_REPORTED_BASELINE_DAYS),
                    coverage=round(min(1.0, coverage), 6),
                    episodes=activity.summaries[:MAX_EPISODES_PER_BUCKET],
                )
            )
        return trends

    # --- coverage -------------------------------------------------------------------------

    @staticmethod
    def _coverage(
        capabilities: Sequence[MetricCapability],
        exclusions: Sequence[Exclusion],
        evaluations: list[SeriesEvaluation],
        findings: list[Finding],
        config: DetectorConfig,
    ) -> list[SignalCoverage]:
        rows: list[SignalCoverage] = []
        for family in SignalFamily:
            caps = [c for c in capabilities if c.family is family]
            best = (
                max(caps, key=lambda c: _CAPABILITY_RANK[c.status]).status
                if caps
                else CapabilityStatus.UNSUPPORTED
            )

            fam_evs = [ev for ev in evaluations if ev.series.rule.family is family]
            evaluated = [
                ev
                for ev in fam_evs
                if np.mean(~np.isnan(ev.values[LATEST_START:])) >= config.bucket_min_coverage
            ]
            level_evaluated = [ev for ev in evaluated if ev.series.rule.kind is RuleKind.LEVEL]
            days = [ev.baselines[0].days for ev in level_evaluated]
            baseline_days = int(np.median(days)) if days else None

            reasons = [
                Reason(code="capability", message=f"{c.signal}: {c.reason}")
                for c in caps
                if c.reason and c.status is not CapabilityStatus.SUPPORTED
            ]
            errors = [e for e in exclusions if e.family is family]
            reasons += [Reason(code=e.code, message=e.message) for e in errors]

            if best is CapabilityStatus.UNSUPPORTED:
                status = SignalStatus.UNSUPPORTED
            elif any(f.family is family for f in findings):
                status = SignalStatus.ANOMALOUS
            elif errors and not evaluated:
                status = SignalStatus.SOURCE_ERROR
            elif not evaluated:
                status = SignalStatus.INSUFFICIENT_DATA
                reasons.append(
                    Reason(
                        code="coverage_low",
                        message="No series with ≥ 50 % coverage in the latest day.",
                    )
                )
            elif level_evaluated and all(not ev.baselines[0].available for ev in level_evaluated):
                status = SignalStatus.INSUFFICIENT_DATA
                reasons.append(
                    Reason(
                        code="insufficient_baseline",
                        message=f"{baseline_days} adequate baseline days "
                        f"(< {config.baseline_min_adequate_days}); relative detection "
                        "unavailable, absolute checks ran.",
                    )
                )
            elif errors:
                status = SignalStatus.SOURCE_ERROR
            else:
                status = SignalStatus.NO_ANOMALY

            rows.append(
                SignalCoverage(
                    family=family,
                    status=status,
                    capability=best,
                    evaluated_series=len(evaluated),
                    total_series=len(fam_evs),
                    baseline_days=baseline_days,
                    reasons=reasons,
                )
            )
        return rows


# --- finding helpers ----------------------------------------------------------------------


def _finding_threshold(
    ev: SeriesEvaluation, ep: Episode, th: SignalThresholds | None
) -> float | None:
    """The heuristic threshold the episode crossed, or None for relative-only findings."""
    kind = ev.series.rule.kind
    if kind is RuleKind.SHORTFALL:
        return SHORTFALL_MIN_VALUE
    if kind is not RuleKind.LEVEL:
        return EVENT_MIN_VALUE
    if not ep.abs_level or th is None:
        return None
    if ep.abs_level == ABS_LEVEL_CRITICAL and th.absolute_critical is not None:
        return th.absolute_critical
    return th.absolute_high


def _expected_value(
    ev: SeriesEvaluation, ep: Episode, baseline_ok: bool, config: DetectorConfig
) -> ExpectedValue | None:
    if not baseline_ok or np.isnan(ev.median[ep.peak]):
        return None
    median = float(ev.median[ep.peak])
    spread = config.z_threshold * float(ev.scale[ep.peak])
    return ExpectedValue(
        median=median, lower=max(0.0, median - spread), upper=median + spread, unit=ev.series.unit
    )


def _confidence(reasons: list[Reason]) -> ConfidenceLevel:
    if any(r.code.endswith(LOW_CONFIDENCE_SUFFIX) for r in reasons):
        return ConfidenceLevel.LOW
    if reasons:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH


def _prior_episode_days(ev: SeriesEvaluation, ep: Episode) -> list[int]:
    """Earlier trend buckets (never the latest day) with another episode of this series."""
    other_buckets = {b for other in ev.episodes if other is not ep for b in other.buckets()}
    return sorted(other_buckets - {0} - ep.buckets())


def _recurrence(prior_days: int) -> Recurrence:
    if not prior_days:
        return Recurrence.NEW
    if prior_days >= RECURRING_MIN_PRIOR_DAYS:
        return Recurrence.RECURRING
    return Recurrence.REPEATED


def _finding_state(ep: Episode, config: DetectorConfig) -> FindingState:
    # Within one merge gap of the window end the episode may still be continuing.
    if ep.end >= TREND_STEPS - config.max_merge_gap_steps:
        return FindingState.ONGOING
    return FindingState.RESOLVED


def _related_finding_ids(
    findings: list[Finding], hosts: dict[str, set[str]], config: DetectorConfig
) -> dict[str, set[str]]:
    """Overlapping findings on the same entity, the same service, or a shared host."""
    slack = timedelta(minutes=config.relation_slack_minutes)
    related: dict[str, set[str]] = defaultdict(set)
    for i, a in enumerate(findings):
        for b in findings[i + 1 :]:
            overlap = a.start - slack < b.end and b.start - slack < a.end
            if not overlap:
                continue

            same_entity = a.entity.key == b.entity.key
            same_service = (
                a.entity.kind is b.entity.kind is EntityKind.ROUTE
                and a.entity.labels.get(JOB_LABEL) == b.entity.labels.get(JOB_LABEL)
            )
            shared_host = bool(hosts[a.finding_id] & hosts[b.finding_id])
            if same_entity or same_service or shared_host:
                related[a.finding_id].add(b.finding_id)
                related[b.finding_id].add(a.finding_id)
    return related


# --- trend helpers ------------------------------------------------------------------------


def _bucket_activity(context: _TrendContext, bucket_index: int) -> _BucketActivity:
    """Observed/anomalous entity-minutes and episode summaries of one trend bucket."""
    lo, hi = _bucket_bounds(bucket_index)
    activity = _BucketActivity()
    for entity_key in sorted(context.present):
        observed = np.zeros(STEPS_PER_DAY, dtype=bool)
        anomalous = np.zeros(STEPS_PER_DAY, dtype=bool)
        for ei, ev in context.entities[entity_key]:
            observed |= ~np.isnan(ev.values[lo:hi])
            for ep in ev.episodes:
                start, end = max(ep.start, lo), min(ep.end, hi)
                if start >= end:
                    continue

                anomalous[start - lo : end - lo] = True
                activity.affected.add(entity_key)
                severity = severity_from_points(ep.severity_points, ev.series.rule.severity_cap)
                activity.peak = _more_severe(activity.peak, severity)
                activity.summaries.append(
                    _episode_summary(context, bucket_index, ei, ev, ep, severity)
                )
        activity.observed_minutes += int(observed.sum()) * MINUTES_PER_STEP
        activity.anomalous_minutes += int((anomalous & observed).sum()) * MINUTES_PER_STEP
    return activity


def _more_severe(current: Severity | None, candidate: Severity) -> Severity:
    if current is None or SEVERITY_ORDER.index(candidate) > SEVERITY_ORDER.index(current):
        return candidate
    return current


def _episode_summary(
    context: _TrendContext,
    bucket_index: int,
    evaluation_index: int,
    ev: SeriesEvaluation,
    ep: Episode,
    severity: Severity,
) -> EpisodeSummary:
    """Summary of the part of an episode that falls inside one bucket."""
    lo, hi = _bucket_bounds(bucket_index)
    start, end = max(ep.start, lo), min(ep.end, hi)
    rule = ev.series.rule
    in_bucket = ev.values[start:end]

    # Only latest-day episodes have a finding (and evidence) to link to.
    finding = (
        context.finding_for_episode.get((evaluation_index, id(ep))) if bucket_index == 0 else None
    )
    peak_observed = float(np.nanmax(in_bucket)) if ep.up else float(np.nanmin(in_bucket))
    expected_median = _opt(float(ev.median[ep.peak])) if lo <= ep.peak < hi else None

    return EpisodeSummary(
        episode_id=episode_id(
            context.key, ev.series.entity.key, rule.name, context.at(ep.start).isoformat()
        ),
        finding_id=finding,
        entity=ev.series.entity,
        family=rule.family,
        signal=rule.name,
        start=context.at(start),
        end=context.at(end),
        severity=severity,
        peak_observed=peak_observed,
        expected_median=expected_median,
        unit=ev.series.unit,
    )


def _median_baseline_days(evaluations: list[SeriesEvaluation], bucket_index: int) -> int:
    lo, hi = _bucket_bounds(bucket_index)
    baseline_days = [
        ev.baselines[bucket_index].days
        for ev in evaluations
        if ev.series.rule.kind is RuleKind.LEVEL and np.any(~np.isnan(ev.values[lo:hi]))
    ]
    return int(np.median(baseline_days)) if baseline_days else 0


def _bucket_status(
    coverage: float, baseline_days: int, config: DetectorConfig
) -> TrendBucketStatus:
    if coverage < config.bucket_min_coverage:
        return TrendBucketStatus.INSUFFICIENT_DATA
    if baseline_days < config.baseline_min_adequate_days:
        return TrendBucketStatus.INSUFFICIENT_BASELINE
    return TrendBucketStatus.OK


def trend_summary(trends: list[DailyTrend]) -> TrendSummary:
    ok = {t.bucket_index: t for t in trends if t.status is TrendBucketStatus.OK}
    recent = [ok[b] for b in RECENT_BUCKETS if b in ok]
    previous = [ok[b] for b in PREVIOUS_BUCKETS if b in ok]

    def share(ts: list[DailyTrend]) -> float | None:
        observed = sum(t.observed_entity_minutes for t in ts)
        return sum(t.anomalous_minutes for t in ts) / observed if observed else None

    r, p = share(recent), share(previous)
    rec_eps = sum(t.episode_count for t in recent)
    prev_eps = sum(t.episode_count for t in previous)
    if (
        len(recent) < MIN_OK_BUCKETS_PER_HALF
        or len(previous) < MIN_OK_BUCKETS_PER_HALF
        or r is None
        or p is None
    ):
        return TrendSummary(
            direction=TrendDirection.INCONCLUSIVE,
            confidence=ConfidenceLevel.LOW,
            recent_share=r,
            previous_share=p,
            recent_episodes=rec_eps,
            previous_episodes=prev_eps,
            reason=f"Only {len(recent)} recent and {len(previous)} earlier days have a full "
            f"baseline and coverage (need ≥ {MIN_OK_BUCKETS_PER_HALF} each).",
        )

    diff = r - p
    if r >= TREND_CHANGE_FACTOR * p and diff >= TREND_MIN_SHARE_CHANGE:
        direction, text = TrendDirection.WORSENING, "higher"
    elif p >= TREND_CHANGE_FACTOR * r and -diff >= TREND_MIN_SHARE_CHANGE:
        direction, text = TrendDirection.IMPROVING, "lower"
    else:
        direction, text = TrendDirection.STABLE, "similar"

    return TrendSummary(
        direction=direction,
        confidence=_trend_confidence(len(ok)),
        recent_share=round(r, 6),
        previous_share=round(p, 6),
        recent_episodes=rec_eps,
        previous_episodes=prev_eps,
        reason=f"Anomalous share {r:.2%} over the last 7 days vs {p:.2%} over the previous 7 "
        f"({text}); {rec_eps} vs {prev_eps} episodes.",
    )


def _trend_confidence(ok_buckets: int) -> ConfidenceLevel:
    if ok_buckets == HIGH_CONFIDENCE_OK_BUCKETS:
        return ConfidenceLevel.HIGH
    if ok_buckets >= MEDIUM_CONFIDENCE_OK_BUCKETS:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _sample_coverage(values: list[float | None]) -> float:
    return sum(v is not None for v in values) / len(values) if values else 0.0


def _slice_series(
    s: AnalysisSeries, values: np.ndarray, start: datetime, windows: AnalysisWindows
) -> MetricSeries:
    vals = [_opt(v) for v in values]
    return MetricSeries(
        series_id=series_id(s.query, f"{s.entity.key}|{s.rule.name}"),
        family=s.rule.family,
        signal=s.rule.name,
        entity=s.entity,
        labels=dict(s.source.labels),
        unit=s.unit,
        query=s.query,
        step_seconds=windows.step_seconds,
        start=start,
        values=vals,
        coverage=_sample_coverage(vals),
    )


def _slice_series_raw(src: MetricSeries, values: np.ndarray, start: datetime) -> MetricSeries:
    vals = [_opt(v) for v in values]
    return src.model_copy(
        update={"start": start, "values": vals, "coverage": _sample_coverage(vals)}
    )


__all__ = ["Dir", "RobustDetector", "Unit", "format_value", "trend_summary"]
