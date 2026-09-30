"""RobustDetector: deterministic, AI-free implementation of the Detector interface (T005)."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import numpy as np

from app.analysis.baseline import STEPS_PER_DAY
from app.analysis.derive import AnalysisSeries, derive
from app.analysis.detect import TREND_STEPS, Episode, SeriesEvaluation, evaluate
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
from app.domain.detector_config import DetectorConfig
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
LATEST_START = TREND_STEPS - STEPS_PER_DAY

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
    severity = (
        Severity.LOW
        if points <= 1
        else Severity.MEDIUM
        if points == 2
        else Severity.HIGH
        if points <= 4
        else Severity.CRITICAL
    )
    if cap is not None and SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index(cap):
        return cap
    return severity


def _opt(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


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
            return mappings.get(f"swarm:{labels.get('service_name', '')}", set())
        case EntityKind.ROUTE:
            return mappings.get(f"otel:{labels.get('job', '')}", set())
    return set()


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
        trend_start = windows.trend.start
        mappings: dict[str, set[str]] = defaultdict(set)
        for m in collection.mappings:
            prefix = "otel" if m.kind is MappingKind.OTEL_SERVICE else "swarm"
            mappings[f"{prefix}:{m.service}"].add(m.host)

        def at(t: int) -> datetime:
            return trend_start + timedelta(seconds=t * step)

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
        findings.sort(
            key=lambda f: (
                -SEVERITY_ORDER.index(f.severity),
                f.start,
                f.finding_id,
            )
        )
        trends = self._trends(key, evaluations, finding_for_episode, at, windows)
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
        points = ep.severity_points
        severity = severity_from_points(points, rule.severity_cap)
        is_abs = ep.abs_level > 0 or rule.kind is not RuleKind.LEVEL
        threshold: float | None = None
        if rule.kind is not RuleKind.LEVEL:
            threshold = 1.0 if rule.kind is RuleKind.SHORTFALL else 0.5
        elif ep.abs_level and th is not None:
            threshold = (
                th.absolute_critical
                if ep.abs_level == 2 and th.absolute_critical is not None
                else th.absolute_high
            )
        peak_value = float(ev.values[ep.peak])
        expected = None
        if base.available and not np.isnan(ev.median[ep.peak]):
            med = float(ev.median[ep.peak])
            spread = config.z_threshold * float(ev.scale[ep.peak])
            expected = ExpectedValue(
                median=med, lower=max(0.0, med - spread), upper=med + spread, unit=s.unit
            )
        reasons = self._confidence_reasons(ev, ep, base.available, is_abs)
        confidence = (
            ConfidenceLevel.LOW
            if any(r.code.endswith("_low") for r in reasons)
            else ConfidenceLevel.MEDIUM
            if reasons
            else ConfidenceLevel.HIGH
        )
        prior = sorted(
            {b for other in ev.episodes if other is not ep for b in other.buckets()}
            - {0}
            - ep.buckets()
        )
        attributes = dict(s.attributes)
        if rule.name.endswith("latency_p95") and peak_value >= 10.0:
            attributes["latency_bound"] = ">= 10 s (top histogram bucket)"
        evs = self._evidence(fid, ev, ep, windows, threshold, rule.label, config.z_threshold)
        method = DetectionMethod.ABSOLUTE if is_abs else DetectionMethod.RELATIVE
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
            severity=severity,
            severity_points=points,
            confidence=confidence,
            confidence_reasons=reasons,
            observed=ObservedValue(value=peak_value, unit=s.unit),
            expected=expected,
            peak_score=None if ep.peak_z is None or not base.available else round(ep.peak_z, 2),
            threshold=threshold,
            baseline_days=base.days if rule.kind is RuleKind.LEVEL else None,
            baseline_mode=base.mode,
            evidence_ids=[e.evidence_id for e in evs],
            attributes=attributes,
            state=(
                FindingState.ONGOING
                if ep.end >= TREND_STEPS - config.max_merge_gap_steps
                else FindingState.RESOLVED
            ),
            recurrence=(
                Recurrence.NEW
                if not prior
                else Recurrence.RECURRING
                if len(prior) >= 3
                else Recurrence.REPEATED
            ),
            prior_episode_days=len(prior),
        )
        if method is DetectionMethod.RELATIVE and (expected is None or finding.peak_score is None):
            raise AssertionError("relative finding without baseline")  # pragma: no cover
        return finding, evs

    @staticmethod
    def _confidence_reasons(
        ev: SeriesEvaluation, ep: Episode, baseline_ok: bool, is_abs: bool
    ) -> list[Reason]:
        reasons: list[Reason] = []
        base = ev.baseline_for_step(ep.peak)
        if ev.series.rule.kind is RuleKind.LEVEL:
            if not baseline_ok:
                reasons.append(
                    Reason(
                        code="baseline_low",
                        message=f"{base.days} adequate baseline days (< 3); absolute check only.",
                    )
                )
            elif base.days < 7:
                reasons.append(
                    Reason(
                        code="baseline_short",
                        message=f"{base.days} adequate baseline days (< 7).",
                    )
                )
        lo = max(0, ep.start - COVERAGE_PAD_STEPS)
        hi = min(TREND_STEPS, ep.end + COVERAGE_PAD_STEPS)
        coverage = float(np.mean(~np.isnan(ev.values[lo:hi])))
        if coverage < 0.7:
            reasons.append(
                Reason(code="coverage_low", message=f"Coverage {coverage:.0%} in episode ± 1 h.")
            )
        elif coverage < 0.9:
            reasons.append(
                Reason(
                    code="coverage_reduced", message=f"Coverage {coverage:.0%} in episode ± 1 h."
                )
            )
        if ev.series.volume is not None:
            vol = ev.series.volume[ev.offset + ep.start : ev.offset + ep.end]
            flagged = vol[~np.isnan(vol)]
            if flagged.size and float(flagged.min()) < 300:
                reasons.append(
                    Reason(
                        code="request_volume",
                        message=f"As few as {flagged.min():.0f} requests per 5-min step (< 300).",
                    )
                )
        if ev.series.rule.kind is RuleKind.LEVEL and ep.anomalous_steps < 6 and not is_abs:
            reasons.append(
                Reason(code="episode_short", message=f"{ep.anomalous_steps} anomalous steps (< 6).")
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
        for extra in s.extra_evidence[:2]:
            values = np.array([np.nan if v is None else v for v in extra.values])
            ms = _slice_series_raw(extra, values[ev.offset + lo : ev.offset + hi], start)
            out.append(
                Evidence(evidence_id=evidence_id(fid, ms.series_id), finding_id=fid, series=ms)
            )
        return out

    # --- relations ------------------------------------------------------------------------

    @staticmethod
    def _relate(
        findings: list[Finding], mappings: dict[str, set[str]], config: DetectorConfig
    ) -> list[Finding]:
        slack = timedelta(minutes=config.relation_slack_minutes)
        hosts = {f.finding_id: hosts_for(f.entity, mappings) for f in findings}
        related: dict[str, set[str]] = defaultdict(set)
        for i, a in enumerate(findings):
            for b in findings[i + 1 :]:
                overlap = a.start - slack < b.end and b.start - slack < a.end
                if not overlap:
                    continue
                same_entity = a.entity.key == b.entity.key
                same_service = (
                    a.entity.kind is b.entity.kind is EntityKind.ROUTE
                    and a.entity.labels.get("job") == b.entity.labels.get("job")
                )
                shared_host = bool(hosts[a.finding_id] & hosts[b.finding_id])
                if same_entity or same_service or shared_host:
                    related[a.finding_id].add(b.finding_id)
                    related[b.finding_id].add(a.finding_id)
        out = []
        for f in findings:
            attrs = dict(f.attributes)
            if f.entity.kind in (EntityKind.ROUTE, EntityKind.SERVICE) and hosts[f.finding_id]:
                attrs["host"] = ",".join(sorted(hosts[f.finding_id]))
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
        windows: AnalysisWindows,
    ) -> list[DailyTrend]:
        entities: dict[str, list[tuple[int, SeriesEvaluation]]] = defaultdict(list)
        for i, ev in enumerate(evaluations):
            entities[ev.series.entity.key].append((i, ev))
        present = {
            k
            for k, evs in entities.items()
            if any(np.any(~np.isnan(e.series.values)) for _, e in evs)
        }
        trends: list[DailyTrend] = []
        for b in range(TREND_DAYS):
            lo = (TREND_DAYS - 1 - b) * STEPS_PER_DAY
            hi = lo + STEPS_PER_DAY
            observed_minutes = 0
            anomalous_minutes = 0
            affected: set[str] = set()
            summaries: list[EpisodeSummary] = []
            peak: Severity | None = None
            for ekey in sorted(present):
                evs = entities[ekey]
                observed = np.zeros(STEPS_PER_DAY, dtype=bool)
                anomalous = np.zeros(STEPS_PER_DAY, dtype=bool)
                for ei, ev in evs:
                    observed |= ~np.isnan(ev.values[lo:hi])
                    for ep in ev.episodes:
                        s, e = max(ep.start, lo), min(ep.end, hi)
                        if s >= e:
                            continue
                        anomalous[s - lo : e - lo] = True
                        affected.add(ekey)
                        sev = severity_from_points(ep.severity_points, ev.series.rule.severity_cap)
                        if peak is None or SEVERITY_ORDER.index(sev) > SEVERITY_ORDER.index(peak):
                            peak = sev
                        summaries.append(
                            EpisodeSummary(
                                episode_id=episode_id(
                                    key, ekey, ev.series.rule.name, at(ep.start).isoformat()
                                ),
                                finding_id=finding_for_episode.get((ei, id(ep)))
                                if b == 0
                                else None,
                                entity=ev.series.entity,
                                family=ev.series.rule.family,
                                signal=ev.series.rule.name,
                                start=at(s),
                                end=at(e),
                                severity=sev,
                                peak_observed=float(np.nanmax(ev.values[s:e]))
                                if ep.up
                                else float(np.nanmin(ev.values[s:e])),
                                expected_median=_opt(float(ev.median[ep.peak]))
                                if lo <= ep.peak < hi
                                else None,
                                unit=ev.series.unit,
                            )
                        )
                observed_minutes += int(observed.sum()) * 5
                anomalous_minutes += int((anomalous & observed).sum()) * 5
            baseline_days = [
                ev.baselines[b].days
                for ev in evaluations
                if ev.series.rule.kind is RuleKind.LEVEL and np.any(~np.isnan(ev.values[lo:hi]))
            ]
            used = int(np.median(baseline_days)) if baseline_days else 0
            coverage = observed_minutes / (len(present) * 1440) if present else 0.0
            status = (
                TrendBucketStatus.INSUFFICIENT_DATA
                if coverage < 0.5
                else TrendBucketStatus.INSUFFICIENT_BASELINE
                if used < 3
                else TrendBucketStatus.OK
            )
            summaries.sort(key=lambda e: (-SEVERITY_ORDER.index(e.severity), e.start, e.episode_id))
            trends.append(
                DailyTrend(
                    bucket_index=b,
                    window=TimeRange(start=at(lo), end=at(hi)),
                    status=status,
                    episode_count=len(summaries),
                    anomalous_minutes=anomalous_minutes,
                    peak_severity=peak,
                    affected_entities=sorted(affected),
                    observed_entity_minutes=observed_minutes,
                    anomalous_share=round(anomalous_minutes / observed_minutes, 6)
                    if observed_minutes
                    else None,
                    baseline_days_used=min(used, 14),
                    coverage=round(min(1.0, coverage), 6),
                    episodes=summaries[:MAX_EPISODES_PER_BUCKET],
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
                else (CapabilityStatus.UNSUPPORTED)
            )
            fam_evs = [ev for ev in evaluations if ev.series.rule.family is family]
            latest = [ev.values[LATEST_START:] for ev in fam_evs]
            evaluated = [
                ev
                for ev, v in zip(fam_evs, latest, strict=True)
                if np.mean(~np.isnan(v)) >= config.bucket_min_coverage
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
                        message=f"{baseline_days} adequate baseline days (< 3); relative detection "
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


def trend_summary(trends: list[DailyTrend]) -> TrendSummary:
    ok = {t.bucket_index: t for t in trends if t.status is TrendBucketStatus.OK}
    recent = [ok[b] for b in range(7) if b in ok]
    previous = [ok[b] for b in range(7, 14) if b in ok]

    def share(ts: list[DailyTrend]) -> float | None:
        observed = sum(t.observed_entity_minutes for t in ts)
        return sum(t.anomalous_minutes for t in ts) / observed if observed else None

    r, p = share(recent), share(previous)
    rec_eps = sum(t.episode_count for t in recent)
    prev_eps = sum(t.episode_count for t in previous)
    confidence = (
        ConfidenceLevel.HIGH
        if len(ok) == 14
        else ConfidenceLevel.MEDIUM
        if len(ok) >= 10
        else ConfidenceLevel.LOW
    )
    if len(recent) < 4 or len(previous) < 4 or r is None or p is None:
        return TrendSummary(
            direction=TrendDirection.INCONCLUSIVE,
            confidence=ConfidenceLevel.LOW,
            recent_share=r,
            previous_share=p,
            recent_episodes=rec_eps,
            previous_episodes=prev_eps,
            reason=f"Only {len(recent)} recent and {len(previous)} earlier days have a full "
            "baseline and coverage (need ≥ 4 each).",
        )
    diff = r - p
    if r >= 1.5 * p and diff >= 0.005:
        direction, text = TrendDirection.WORSENING, "higher"
    elif p >= 1.5 * r and -diff >= 0.005:
        direction, text = TrendDirection.IMPROVING, "lower"
    else:
        direction, text = TrendDirection.STABLE, "similar"
    return TrendSummary(
        direction=direction,
        confidence=confidence,
        recent_share=round(r, 6),
        previous_share=round(p, 6),
        recent_episodes=rec_eps,
        previous_episodes=prev_eps,
        reason=f"Anomalous share {r:.2%} over the last 7 days vs {p:.2%} over the previous 7 "
        f"({text}); {rec_eps} vs {prev_eps} episodes.",
    )


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
        coverage=sum(v is not None for v in vals) / len(vals) if vals else 0.0,
    )


def _slice_series_raw(src: MetricSeries, values: np.ndarray, start: datetime) -> MetricSeries:
    vals = [_opt(v) for v in values]
    return src.model_copy(
        update={
            "start": start,
            "values": vals,
            "coverage": sum(v is not None for v in vals) / len(vals) if vals else 0.0,
        }
    )


__all__ = ["Dir", "RobustDetector", "Unit", "format_value", "trend_summary"]
