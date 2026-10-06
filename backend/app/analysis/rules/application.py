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
    ),
    Rule(
        "app_unhandled_error_rate",
        F.APP_ERRORS,
        "Unhandled errors",
        thresholds="app_unhandled_error_rate",
        title_up="Unhandled errors (crashes) increase",
    ),
    Rule(
        "app_error_users",
        F.APP_ERRORS,
        "Users hitting errors",
        thresholds="app_error_users",
        title_up="More users hitting errors",
    ),
    Rule(
        "app_transaction_rate",
        F.APP_PERFORMANCE,
        "Transaction throughput",
        thresholds="request_rate",
        direction=Dir.BOTH,
        title_up="Transaction throughput increase",
        title_down="Transaction throughput drop",
    ),
    Rule(
        "app_transaction_failure_ratio",
        F.APP_PERFORMANCE,
        "Failed transactions",
        thresholds="app_transaction_failure_ratio",
        volume_guard=True,
        volume_noun="transactions",
        title_up="Failed transaction share increase",
    ),
    Rule(
        "app_duration_p95",
        F.APP_PERFORMANCE,
        "Transaction duration (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
        volume_noun="transactions",
    ),
)
