"""Reverse-proxy rules: connections, proxy availability and upstream health."""

from app.analysis.rules.base import Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
PROXY_RULES = (
    Rule(
        "proxy_connections_active",
        F.PROXY,
        "Open proxy connections",
        thresholds="proxy_connections_active",
        severity_cap=Severity.MEDIUM,
        title_up="Open proxy connections above expected range",
    ),
    Rule(
        "proxy_connections_dropped",
        F.PROXY,
        "Dropped proxy connections",
        thresholds="proxy_connections_dropped",
    ),
    Rule(
        "proxy_down",
        F.PROXY,
        "Proxy status unavailable",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Proxy status unreadable by its exporter",
    ),
    Rule(
        "upstream_unavailable",
        F.PROXY,
        "Upstream unavailable",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Upstream server unavailable or unhealthy",
    ),
)
