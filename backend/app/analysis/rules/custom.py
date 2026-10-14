"""Analysis rules built from a project's custom rules (T020)."""

from collections.abc import Sequence

from app.analysis.rules.base import Dir, Rule, RuleKind
from app.domain.common import Severity, SignalFamily, Unit
from app.domain.custom_rules import ChangeDirection, CustomRule, CustomRuleMode, ThresholdOp
from app.domain.detector_config import SignalThresholds

DEFAULT_MIN_CHANGE_FACTOR = 1.5
"""Baseline deviations of a rule without min_change(_factor) must reach 1.5x (or 1/1.5x)."""
DEFAULT_EVENT_SEVERITY = Severity.HIGH

_ABS_FLOOR = {
    Unit.RATIO: 0.005,
    Unit.SECONDS: 0.001,
}
"""Scale floor for near-constant baselines; the relative floor (5 % of the median) still
applies on top."""
DEFAULT_ABS_FLOOR = 0.01

# Points that map to each severity (see engine.SEVERITY_MAX_POINTS).
EVENT_POINTS = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 5}

_DIR = {ChangeDirection.UP: Dir.UP, ChangeDirection.DOWN: Dir.DOWN, ChangeDirection.BOTH: Dir.BOTH}


def _thresholds(rule: CustomRule) -> SignalThresholds:
    min_rel = rule.min_change_factor
    if min_rel is None and rule.min_change is None:
        min_rel = DEFAULT_MIN_CHANGE_FACTOR
    return SignalThresholds(
        min_abs_effect=rule.min_change,
        min_rel_factor=min_rel,
        abs_floor=_ABS_FLOOR.get(rule.unit, DEFAULT_ABS_FLOOR),
        absolute_high=rule.threshold,
        absolute_critical=rule.critical_threshold,
        absolute_min_minutes=rule.min_duration_minutes,
    )


def custom_rule(rule: CustomRule) -> Rule:
    if rule.mode is CustomRuleMode.EVENT:
        severity = rule.max_severity or DEFAULT_EVENT_SEVERITY
        return Rule(
            rule.signal,
            SignalFamily.CUSTOM,
            rule.title,
            kind=RuleKind.EVENT,
            severity_cap=severity,
            event_points=EVENT_POINTS[severity],
            custom=True,
        )
    return Rule(
        rule.signal,
        SignalFamily.CUSTOM,
        rule.title,
        direction=_DIR[rule.direction],
        severity_cap=rule.max_severity,
        own_thresholds=_thresholds(rule),
        relative=rule.mode is not CustomRuleMode.THRESHOLD,
        absolute_below=rule.threshold_op is ThresholdOp.BELOW,
        custom=True,
    )


def custom_rules(rules: Sequence[CustomRule]) -> dict[str, Rule]:
    """Analysis rules by signal name."""
    return {r.signal: custom_rule(r) for r in rules}
