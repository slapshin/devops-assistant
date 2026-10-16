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
        summary="The proxy held more open connections than usual.",
        help=(
            "Slow upstreams make connections pile up; check upstream latency, keep-alive "
            "settings and whether the traffic increased."
        ),
    ),
    Rule(
        "proxy_connections_dropped",
        F.PROXY,
        "Dropped proxy connections",
        thresholds="proxy_connections_dropped",
        summary="The proxy dropped connections, so clients saw resets or failures.",
        help=(
            "Check connection and worker limits (worker_connections, maxconn), file "
            "descriptor limits and the proxy's error log."
        ),
    ),
    Rule(
        "proxy_down",
        F.PROXY,
        "Proxy status unavailable",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Proxy status unreadable by its exporter",
        summary="The exporter could not read the proxy's status, so the proxy may be down.",
        help=(
            "Check that the proxy process runs and that its status endpoint is reachable by "
            "the exporter."
        ),
    ),
    Rule(
        "upstream_unavailable",
        F.PROXY,
        "Upstream unavailable",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Upstream server unavailable or unhealthy",
        summary="The proxy marked an upstream server as down or unhealthy.",
        help=(
            "Check the upstream server itself (process, health check endpoint, network) and "
            "the proxy's health check configuration."
        ),
    ),
)
