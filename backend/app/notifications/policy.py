"""When a notification target is delivered to (T025 policy, per target since T026)."""

from app.domain.notifications import SEVERITY_ORDER, NotificationPolicy, NotifyWhen
from app.notifications.message import ReportMessage


def has_problem(policy: NotificationPolicy, message: ReportMessage) -> bool:
    """A failed or partial analysis, or a finding at or above the policy's minimum severity."""
    if message.failed or message.partial:
        return True
    floor = SEVERITY_ORDER.index(policy.min_severity)
    return message.max_severity is not None and SEVERITY_ORDER.index(message.max_severity) >= floor


def skip_reason(policy: NotificationPolicy, message: ReportMessage) -> str | None:
    """Why the policy does not deliver this report; None when it does."""
    if policy.when is NotifyWhen.ALWAYS or has_problem(policy, message):
        return None
    return f"no finding at or above {policy.min_severity.value} severity"
