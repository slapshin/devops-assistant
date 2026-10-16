"""Application rules (Sentry): error spikes, crashes, affected users and transaction health.

Sentry's failure_rate() counts every non-ok span status, including client-side ones such as
not_found or unauthenticated, so the failure ratio has its own, looser thresholds.
"""

from app.analysis.rules.base import Dir, Rule
from app.domain.common import SignalFamily

F = SignalFamily
APPLICATION_RULES = (
    Rule(
        "app_error_rate",
        F.APP_ERRORS,
        "Error events",
        thresholds="app_error_rate",
        title_up="Error spike",
        summary="The application reported more error events to Sentry.",
        help=(
            "Open the listed issues in Sentry, starting with the most frequent, and check "
            "for a release or dependency failure at the start."
        ),
    ),
    Rule(
        "app_unhandled_error_rate",
        F.APP_ERRORS,
        "Unhandled errors",
        thresholds="app_unhandled_error_rate",
        title_up="Unhandled errors (crashes) increase",
        summary="More errors were unhandled, i.e. crashes or uncaught exceptions.",
        help=(
            "Open the unhandled issues in Sentry and check whether a new release introduced them."
        ),
    ),
    Rule(
        "app_error_users",
        F.APP_ERRORS,
        "Users hitting errors",
        thresholds="app_error_users",
        title_up="More users hitting errors",
        summary="More distinct users hit errors, so the impact spread.",
        help=(
            "Open the issues with the most affected users in Sentry and check the affected "
            "browsers, releases or regions."
        ),
    ),
    Rule(
        "app_transaction_rate",
        F.APP_PERFORMANCE,
        "Transaction throughput",
        thresholds="request_rate",
        direction=Dir.BOTH,
        title_up="Transaction throughput increase",
        title_down="Transaction throughput drop",
        summary="Transaction throughput moved outside its usual range.",
        help=(
            "A drop can mean users cannot reach the application or the SDK stopped sending; "
            "a rise new load. Check errors in the same window."
        ),
    ),
    Rule(
        "app_transaction_failure_ratio",
        F.APP_PERFORMANCE,
        "Failed transactions",
        thresholds="app_transaction_failure_ratio",
        volume_guard=True,
        volume_noun="transactions",
        title_up="Failed transaction share increase",
        summary="A larger share of transactions finished with a non-ok status.",
        help=(
            "Open the failing transactions in Sentry and group by status; this also counts "
            "client-side statuses such as not_found."
        ),
    ),
    Rule(
        "app_duration_p95",
        F.APP_PERFORMANCE,
        "Transaction duration (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
        volume_noun="transactions",
        summary="The slowest 5 % of transactions got slower.",
        help=(
            "Open slow transactions in Sentry and look at their spans for slow queries or "
            "HTTP calls; compare with the release."
        ),
    ),
)
