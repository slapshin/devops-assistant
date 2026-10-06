"""CDN edge rules (Cloudflare): traffic, errors, origin health, cache and time to first byte."""

from app.analysis.rules.base import Dir, Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
EDGE_RULES = (
    Rule(
        "edge_request_rate",
        F.EDGE,
        "Edge request rate",
        thresholds="request_rate",
        direction=Dir.BOTH,
        title_up="Edge traffic increase",
        title_down="Edge traffic drop",
    ),
    Rule(
        "edge_server_error_ratio",
        F.EDGE,
        "Edge server error rate (5xx)",
        thresholds="server_error_ratio",
        volume_guard=True,
    ),
    Rule(
        "edge_origin_error_ratio",
        F.EDGE,
        "Origin error rate (520-530)",
        thresholds="edge_origin_error_ratio",
        volume_guard=True,
        title_up="Origin unreachable or failing (Cloudflare 52x)",
    ),
    Rule(
        "edge_not_found_rate",
        F.EDGE,
        "Edge 404 responses",
        thresholds="not_found_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Edge 404 increase",
    ),
    Rule(
        "edge_client_error_rate",
        F.EDGE,
        "Edge client errors (4xx excl. 404)",
        thresholds="client_error_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Edge client error increase (4xx, excl. 404)",
    ),
    Rule(
        "edge_cache_hit_ratio",
        F.EDGE,
        "Cache hit share",
        thresholds="edge_cache_hit_ratio",
        direction=Dir.DOWN,
        volume_guard=True,
        severity_cap=Severity.MEDIUM,
        title_down="Cache hit share drop",
    ),
    Rule(
        "edge_ttfb_p95",
        F.EDGE,
        "Edge time to first byte (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
    ),
    Rule(
        "edge_origin_time_p95",
        F.EDGE,
        "Origin response time (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
    ),
)
