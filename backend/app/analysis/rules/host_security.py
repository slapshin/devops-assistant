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
        summary="The agent raised more Wazuh alerts than usual.",
        help=(
            "Open the agent's alerts in the Wazuh dashboard and group by rule to see what "
            "changed; check for scans, updates or misconfigurations."
        ),
    ),
    Rule(
        "hids_high_alert_rate",
        F.HOST_SECURITY,
        "High-level alerts",
        thresholds="hids_high_alert_rate",
        severity_cap=Severity.HIGH,
        title_up="More high-level security alerts (rule level 12+)",
        summary="More high-level alerts (rule level 12+), which Wazuh treats as significant.",
        help=(
            "Review these alerts in the Wazuh dashboard first, and confirm or rule out a "
            "compromise of the agent."
        ),
    ),
    Rule(
        "hids_auth_failure_rate",
        F.HOST_SECURITY,
        "Authentication failures",
        thresholds="hids_auth_failure_rate",
        severity_cap=Severity.HIGH,
        title_up="Surge of authentication failures (possible brute force)",
        summary="More authentication failures on the agent, which may be a brute-force attempt.",
        help=(
            "Check the source IPs and accounts in the alerts, block abusive sources, and "
            "prefer key-based SSH logins."
        ),
    ),
    Rule(
        "fim_change_rate",
        F.FILE_INTEGRITY,
        "File integrity changes",
        thresholds="fim_change_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Burst of file changes (FIM)",
        summary="More monitored files changed on the agent.",
        help=(
            "Check whether a deploy or package update explains the changes; unexpected "
            "changes to binaries or configuration need investigation."
        ),
    ),
)
