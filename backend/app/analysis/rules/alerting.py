"""Alerting rules (Grafana): instances of the team's own alert rules firing, and rules that
cannot evaluate.

A Grafana rule already encodes a threshold someone chose, so any firing step is a finding
(an event), not a deviation from a baseline. Severity follows the rule's ``severity`` label:
the source collects critical, default and info rules as separate signals. Recurrence (from the
14-day trend) marks rules that fire every day, so chronic alerts are not presented as new.
"""

from app.analysis.rules.base import Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
ALERTING_RULES = (
    Rule(
        "alert_firing",
        F.ALERTING,
        "Grafana alert firing",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="Grafana alert firing",
    ),
    Rule(
        "alert_firing_critical",
        F.ALERTING,
        "Grafana alert firing (severity critical)",
        kind=RuleKind.EVENT,
        event_points=3,
        title_up="Critical Grafana alert firing",
    ),
    Rule(
        "alert_firing_info",
        F.ALERTING,
        "Grafana alert firing (severity info)",
        kind=RuleKind.EVENT,
        event_points=1,
        title_up="Informational Grafana alert firing",
    ),
    Rule(
        "alert_nodata",
        F.ALERTING,
        "Grafana alert rule cannot evaluate",
        kind=RuleKind.EVENT,
        event_points=1,
        severity_cap=Severity.LOW,
        title_up="Grafana alert rule has no data or errors",
    ),
)
