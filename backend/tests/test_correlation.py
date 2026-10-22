"""Related metrics (T023): deterministic comparisons of a finding with other collected series."""

import numpy as np

from app.analysis.engine import RobustDetector
from app.analysis.relations import association
from app.domain.common import Entity, EntityKind, SignalFamily, SourceKind
from app.domain.correlation import (
    ComparisonMethod,
    ComparisonTiming,
    RelatedDirection,
    RelatedEvaluation,
    RelatedMetric,
    RelatedState,
    ResourceAssociation,
    SharedInput,
)
from app.domain.detector_config import DetectorConfig, RelatedConfig
from app.domain.interfaces import CollectionResult, DetectionResult
from app.domain.metrics import MetricSeries
from app.sources.prometheus.synthetic import DAY, TASK_ROUTE, N, _SeriesBuilder
from tests.test_analysis import CONFIG, REQUEST, SCOPE, WINDOWS, collect

EPISODE = slice(N - 40, N - 28)
"""A 1 h episode ending 140 min before T, so the window has its full padding."""


def node(instance: str) -> dict[str, str]:
    return {"job": "node", "instance": instance}


def noise(seed: int, sd: float = 0.01, phi: float = 0.0) -> np.ndarray:
    """AR(1) noise: phi > 0 makes successive samples dependent, like real metrics."""
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0, sd, N)
    out = np.empty(N)
    level = 0.0
    for t in range(N):
        level = phi * level + shocks[t]
        out[t] = level
    return out


def diurnal(amplitude: float) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * (np.arange(N) % DAY) / DAY)


def anchor_cpu(seed: int = 0) -> np.ndarray:
    cpu = 0.3 + noise(seed)
    cpu[EPISODE] += 0.4
    return cpu


def build(*series: tuple[str, dict[str, str], np.ndarray]) -> list[MetricSeries]:
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    for signal, labels, values in series:
        builder.add(signal, labels, values)
    return builder.series


def run(series: list[MetricSeries], config: DetectorConfig = CONFIG) -> DetectionResult:
    col = CollectionResult(series=series, exclusions=[])
    return RobustDetector().detect(REQUEST, WINDOWS, [], col, config)


def anchor_of(result: DetectionResult, signal: str, instance: str) -> str:
    return next(
        f.finding_id
        for f in result.findings
        if f.signal == signal and f.entity.labels.get("instance") == instance
    )


def comparisons(result: DetectionResult, finding_id: str) -> list[RelatedMetric]:
    assert result.related_metrics is not None
    return [c for c in result.related_metrics.comparisons if c.finding_id == finding_id]


def compared(result: DetectionResult, finding_id: str, instance: str) -> RelatedMetric | None:
    return next(
        (
            c
            for c in comparisons(result, finding_id)
            if c.candidate.entity.labels.get("instance") == instance
        ),
        None,
    )


def evaluation(result: DetectionResult, finding_id: str) -> RelatedEvaluation:
    assert result.related_metrics is not None
    return next(e for e in result.related_metrics.evaluations if e.finding_id == finding_id)


# --- similarity and direction ------------------------------------------------------------


def test_simultaneous_change_on_the_same_host_is_kept_first() -> None:
    memory = 0.4 + noise(1, 0.005)
    memory[EPISODE] += 0.1
    other = 0.3 + noise(2)
    other[EPISODE] += 0.3
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("memory_utilization", node("a"), memory),
            ("cpu_utilization", node("b"), other),
        )
    )

    kept = comparisons(result, anchor_of(result, "cpu_utilization", "a"))

    assert [(c.candidate.signal, c.association) for c in kept] == [
        ("memory_utilization", ResourceAssociation.SAME_ENTITY),
        ("cpu_utilization", ResourceAssociation.UNKNOWN),
    ]
    first = kept[0]
    assert first.rank == 1 and first.method is ComparisonMethod.DEVIATION_CORRELATION
    assert first.coefficient is not None and first.coefficient > 0.9
    assert first.direction is RelatedDirection.SAME
    assert first.timing is ComparisonTiming.SIMULTANEOUS and first.lag_seconds is None
    assert first.onset_lead_seconds == 0
    assert first.paired_coverage == 1.0 and first.shared_input is SharedInput.NONE


def test_inverse_change_keeps_its_sign() -> None:
    free = 0.6 + noise(1, 0.005)
    free[EPISODE] -= 0.2
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("memory_utilization", node("b"), free),
        )
    )

    kept = compared(result, anchor_of(result, "cpu_utilization", "a"), "b")

    assert kept is not None and kept.coefficient is not None
    assert kept.coefficient < -0.9 and kept.direction is RelatedDirection.OPPOSITE
    assert kept.association is ResourceAssociation.UNKNOWN


def test_lead_and_lag_follow_the_sign_convention() -> None:
    earlier = 0.3 + noise(1)
    earlier[N - 43 : N - 31] += 0.4
    later = 0.3 + noise(2)
    later[N - 37 : N - 25] += 0.4
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("cpu_utilization", node("early"), earlier),
            ("cpu_utilization", node("late"), later),
        )
    )
    anchor = anchor_of(result, "cpu_utilization", "a")

    leads = compared(result, anchor, "early")
    lags = compared(result, anchor, "late")

    assert leads is not None and lags is not None
    assert (leads.timing, leads.lag_seconds, leads.onset_lead_seconds) == (
        ComparisonTiming.CANDIDATE_LEADS,
        900,
        900,
    )
    assert (lags.timing, lags.lag_seconds, lags.onset_lead_seconds) == (
        ComparisonTiming.CANDIDATE_LAGS,
        -900,
        -900,
    )
    assert leads.coefficient is not None and leads.coefficient > 0.9


def test_broad_or_out_of_range_shifts_claim_no_order() -> None:
    plateau = 0.3 + noise(1)
    plateau[N - 64 : N - 4] += 0.4
    beyond = 0.3 + noise(2)
    beyond[N - 50 : N - 38] += 0.4
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("cpu_utilization", node("plateau"), plateau),
            ("cpu_utilization", node("beyond"), beyond),
        )
    )
    anchor = anchor_of(result, "cpu_utilization", "a")

    broad = compared(result, anchor, "plateau")
    far = compared(result, anchor, "beyond")

    assert broad is None or broad.timing not in (
        ComparisonTiming.CANDIDATE_LEADS,
        ComparisonTiming.CANDIDATE_LAGS,
    )
    # 50 min apart, beyond the ±30 min lag range: unshifted, the changes barely overlap.
    assert far is None or far.timing is ComparisonTiming.INCONCLUSIVE


async def test_sub_threshold_change_without_its_own_finding_is_inspectable() -> None:
    caps, col = await collect("incident")
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    assert result.related_metrics is not None
    anchor = next(
        f.finding_id
        for f in result.findings
        if f.signal == "server_error_ratio" and f.entity.kind is EntityKind.ROUTE
    )

    latency = next(c for c in comparisons(result, anchor) if c.candidate.signal == "latency_p95")
    chart = next(
        c for c in result.related_metrics.charts if c.chart_id == latency.candidate.chart_id
    )

    assert not any(f.signal == "latency_p95" for f in result.findings)
    assert latency.candidate.finding_id is None
    assert latency.association is ResourceAssociation.SAME_ENTITY
    assert latency.onset_lead_seconds == 600
    assert chart.series.signal == "latency_p95" and chart.expected and chart.deviation
    assert len(chart.series.values) == (latency.window.end - latency.window.start).seconds // 300


# --- negative controls -------------------------------------------------------------------


def test_unrelated_autocorrelated_and_daily_series_are_not_kept() -> None:
    candidates = [
        ("cpu_utilization", node(f"ar{phi}-{seed}"), 0.3 + diurnal(0.1) + noise(seed, phi=phi))
        for phi in (0.0, 0.5, 0.9)
        for seed in range(10, 20)
    ]
    anchor = 0.3 + diurnal(0.1) + noise(0)
    anchor[EPISODE] += 0.4
    result = run(build(("cpu_utilization", node("a"), anchor), *candidates))

    kept = comparisons(result, anchor_of(result, "cpu_utilization", "a"))

    # A shared daily cycle is removed by the time-of-day baseline; dependent noise may
    # occasionally pass, but never as a strong comparison.
    assert len(kept) <= 1
    assert all(abs(c.coefficient or 0) < 0.8 for c in kept)


def test_change_only_outside_the_episode_is_not_evidence() -> None:
    after = 0.3 + noise(1)
    after[N - 26 : N - 14] += 0.4  # starts 10 min after the episode, block-shaped
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("cpu_utilization", node("after"), after),
        )
    )

    assert compared(result, anchor_of(result, "cpu_utilization", "a"), "after") is None


# --- events, gaps and missing data -------------------------------------------------------


def test_event_near_the_finding_is_an_interval_overlap() -> None:
    ooms = np.zeros(N)
    ooms[N - 35] = 1.0
    quiet_events = np.zeros(N)
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("node_oom_kills", node("a"), ooms),
            ("node_oom_kills", node("b"), quiet_events),
        )
    )
    anchor = anchor_of(result, "cpu_utilization", "a")

    kept = comparisons(result, anchor)

    assert [(c.candidate.signal, c.candidate.entity.labels["instance"]) for c in kept] == [
        ("node_oom_kills", "a")
    ]
    oom = kept[0]
    assert oom.method is ComparisonMethod.INTERVAL_OVERLAP
    assert oom.coefficient is None and oom.direction is None and oom.overlap_steps == 1
    assert oom.candidate.finding_id is not None  # the OOM has its own finding
    assert evaluation(result, anchor).candidates == 1  # all-zero events never qualify


def test_gappy_candidate_is_unavailable_not_filled() -> None:
    sparse = np.full(N, np.nan)
    sparse[: N - DAY] = 0.3 + noise(1)[: N - DAY]
    sparse[EPISODE] = 0.7
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("cpu_utilization", node("sparse"), sparse),
        )
    )
    anchor = anchor_of(result, "cpu_utilization", "a")

    status = evaluation(result, anchor)

    assert compared(result, anchor, "sparse") is None
    assert status.state is RelatedState.EVALUATED and status.retained == 0
    assert [(u.code, u.count) for u in status.unavailable] == [("insufficient_pairs", 1)]


def test_gaps_are_disclosed_on_kept_comparisons() -> None:
    memory = 0.4 + noise(1, 0.005)
    memory[EPISODE] += 0.1
    memory[N - 64 : N - 46] = np.nan
    result = run(
        build(
            ("cpu_utilization", node("a"), anchor_cpu()),
            ("memory_utilization", node("a"), memory),
        )
    )

    kept = compared(result, anchor_of(result, "cpu_utilization", "a"), "a")

    assert kept is not None
    assert {r.code for r in kept.reasons} >= {"paired_coverage_reduced", "gap"}
    assert kept.paired_samples == 60 - 18 and kept.longest_gap_seconds == 18 * 300


def test_short_history_compares_intervals_only() -> None:
    def short(values: np.ndarray) -> np.ndarray:
        out = values.copy()
        out[: N - 2 * DAY] = np.nan
        return out

    cpu = anchor_cpu()
    cpu[EPISODE] = 0.97
    memory = 0.4 + noise(1, 0.005)
    memory[EPISODE] = 0.95
    result = run(
        build(
            ("cpu_utilization", node("a"), short(cpu)),
            ("memory_utilization", node("a"), short(memory)),
        )
    )
    anchor = anchor_of(result, "cpu_utilization", "a")

    kept = comparisons(result, anchor)
    status = evaluation(result, anchor)

    assert "anchor_baseline_unavailable" in {r.code for r in status.reasons}
    assert [c.method for c in kept] == [ComparisonMethod.INTERVAL_OVERLAP]
    assert "baseline_unavailable" in {r.code for r in kept[0].reasons}


# --- shared inputs, associations ---------------------------------------------------------


def test_ratio_operands_are_not_independent_confirmation() -> None:
    requests = 20.0 + 0.5 * noise(1, 1.0)
    requests[EPISODE] *= 3
    errors = np.full(N, np.nan)
    errors[EPISODE] = requests[EPISODE] * 0.1
    result = run(build(("http_requests", TASK_ROUTE, requests), ("http_5xx", TASK_ROUTE, errors)))
    anchor = next(f.finding_id for f in result.findings if f.signal == "server_error_ratio")

    status = evaluation(result, anchor)

    assert "request_rate" not in {c.candidate.signal for c in comparisons(result, anchor)}
    assert status.shared_input == 1
    assert "shared_input" in {r.code for r in status.reasons}


def test_near_identical_series_are_flagged_as_a_possible_shared_input() -> None:
    base = 0.3 + noise(1, phi=0.5)
    copy = base.copy()
    base[EPISODE] += 0.4
    copy[EPISODE] += 0.4
    result = run(
        build(
            ("cpu_utilization", node("a"), base),
            ("cpu_utilization", node("copy"), copy + 0.001),
        )
    )

    kept = compared(result, anchor_of(result, "cpu_utilization", "a"), "copy")

    assert kept is not None and kept.shared_input is SharedInput.POSSIBLE
    assert "near_identical" in {r.code for r in kept.reasons}


def test_associations_need_labels_or_mappings_never_names() -> None:
    def entity(kind: EntityKind, key: str, **labels: str) -> Entity:
        return Entity(kind=kind, key=key, display_name="shop-production", labels=labels)

    node_entity = entity(
        EntityKind.NODE, "node|instance=shop-production", instance="shop-production"
    )
    agent = entity(EntityKind.AGENT, "agent|agent=shop-production", agent="shop-production")
    zone = entity(EntityKind.ZONE, "zone|zone=shop-production", zone="shop-production")
    route = entity(EntityKind.ROUTE, "route|job=shop", job="shop")

    assert association(node_entity, agent, {"shop-production"}, set())[0] is (
        ResourceAssociation.UNKNOWN
    )
    assert association(route, zone, set(), set())[0] is ResourceAssociation.UNKNOWN
    assert association(route, node_entity, {"shop-production"}, {"shop-production"})[0] is (
        ResourceAssociation.SHARED_HOST
    )


async def test_comparisons_record_each_sides_source() -> None:
    caps, col = await collect("incident")
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    assert result.related_metrics is not None

    sides = [s for c in result.related_metrics.comparisons for s in (c.anchor, c.candidate)]

    assert sides and all(s.source is SourceKind.PROMETHEUS for s in sides)
    assert all(
        s.family is not SignalFamily.CUSTOM or s.source is SourceKind.PROMETHEUS for s in sides
    )


# --- budgets, determinism, independence --------------------------------------------------


def test_budgets_disclose_what_was_not_compared() -> None:
    others = [("cpu_utilization", node(f"o{i}"), anchor_cpu(seed=i + 1)) for i in range(4)]
    series = build(("cpu_utilization", node("a"), anchor_cpu()), *others)
    tight = DetectorConfig(
        related=RelatedConfig(max_candidates_per_finding=2, max_evaluations=4, max_per_finding=1)
    )

    result = run(series, tight)
    assert result.related_metrics is not None
    statuses = result.related_metrics.evaluations

    by_state = {e.state for e in statuses}
    assert RelatedState.SKIPPED_BUDGET in by_state
    first = statuses[0]
    assert (first.candidates, first.evaluated, first.not_evaluated) == (4, 2, 2)
    assert first.retained == 1 and first.omitted == 1
    assert "candidates_capped" in {r.code for r in first.reasons}
    assert sum(e.evaluated for e in statuses) == 4


def test_identical_inputs_give_identical_comparisons_and_findings_are_unchanged() -> None:
    series = build(
        ("cpu_utilization", node("a"), anchor_cpu()),
        ("memory_utilization", node("a"), 0.4 + noise(1, 0.005) + 0.1 * (np.arange(N) >= N - 40)),
    )
    no_comparisons = DetectorConfig(related=RelatedConfig(max_evaluations=1, max_per_finding=1))

    first, second = run(series), run(series)
    cheap = run(series, no_comparisons)

    assert first.model_dump() == second.model_dump()
    assert cheap.findings == first.findings and cheap.trends == first.trends
