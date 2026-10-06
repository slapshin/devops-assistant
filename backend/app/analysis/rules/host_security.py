"""Host security rules (Wazuh): surges of HIDS alerts, high-level alerts, authentication
failures and file integrity changes per agent.

An alert surge calls for investigation, not an outage, so these are relative-only and capped
at high; file changes, which routine updates also cause, are capped at medium.
"""

from app.analysis.rules.base import Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
HOST_SECURITY_RULES = (
    Rule(
        "hids_alert_rate",
        F.HOST_SECURITY,
        "Wazuh alerts",
        thresholds="hids_alert_rate",
        severity_cap=Severity.HIGH,
        title_up="Surge of security alerts",
    ),
    Rule(
        "hids_high_alert_rate",
        F.HOST_SECURITY,
        "High-level alerts",
        thresholds="hids_high_alert_rate",
        severity_cap=Severity.HIGH,
        title_up="More high-level security alerts (rule level 12+)",
    ),
    Rule(
        "hids_auth_failure_rate",
        F.HOST_SECURITY,
        "Authentication failures",
        thresholds="hids_auth_failure_rate",
        severity_cap=Severity.HIGH,
        title_up="Surge of authentication failures (possible brute force)",
    ),
    Rule(
        "fim_change_rate",
        F.FILE_INTEGRITY,
        "File integrity changes",
        thresholds="fim_change_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Burst of file changes (FIM)",
    ),
)
