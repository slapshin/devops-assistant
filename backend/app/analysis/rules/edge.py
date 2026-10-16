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
        summary="Requests reaching the CDN edge moved outside their usual range.",
        help=(
            "A drop can mean DNS or client problems; a rise a campaign, crawlers or an "
            "attack. Check the security events of the zone."
        ),
    ),
    Rule(
        "edge_server_error_ratio",
        F.EDGE,
        "Edge server error rate (5xx)",
        thresholds="server_error_ratio",
        volume_guard=True,
        summary="A larger share of edge responses were 5xx errors.",
        help=(
            "Check whether they come from the origin (520-530 errors, origin logs) or from "
            "Cloudflare itself (status page)."
        ),
    ),
    Rule(
        "edge_origin_error_ratio",
        F.EDGE,
        "Origin error rate (520-530)",
        thresholds="edge_origin_error_ratio",
        volume_guard=True,
        title_up="Origin unreachable or failing (Cloudflare 52x)",
        summary="Cloudflare could not reach the origin or got invalid responses (520-530).",
        help=(
            "Check that the origin is up, its firewall allows Cloudflare's IP ranges, TLS "
            "certificates are valid and responses are not timing out."
        ),
    ),
    Rule(
        "edge_not_found_rate",
        F.EDGE,
        "Edge 404 responses",
        thresholds="not_found_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Edge 404 increase",
        summary="More edge requests returned 404 Not Found.",
        help=(
            "Look for broken links after a deploy, removed assets, or scanners probing for paths."
        ),
    ),
    Rule(
        "edge_client_error_rate",
        F.EDGE,
        "Edge client errors (4xx excl. 404)",
        thresholds="client_error_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Edge client error increase (4xx, excl. 404)",
        summary="More edge requests were rejected with 4xx client errors (other than 404).",
        help=(
            "Group by status: 403 often comes from WAF or access rules, 429 from rate "
            "limiting, 401 from authentication."
        ),
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
        summary=(
            "A smaller share of requests was served from the CDN cache, so more load "
            "reaches the origin."
        ),
        help=(
            "Check for a cache purge, changed Cache-Control headers or cookies after a "
            "deploy, and cache-busting query strings."
        ),
    ),
    Rule(
        "edge_ttfb_p95",
        F.EDGE,
        "Edge time to first byte (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
        summary="The edge took longer to send the first byte to the slowest 5 % of visitors.",
        help=(
            "Compare with the origin response time: if both rose, the origin is slow; "
            "otherwise look at cache hit share and edge routing."
        ),
    ),
    Rule(
        "edge_origin_time_p95",
        F.EDGE,
        "Origin response time (p95)",
        thresholds="latency_quantile",
        volume_guard=True,
        summary=(
            "The origin took longer to respond to Cloudflare for the slowest 5 % of requests."
        ),
        help="Check the origin's application latency, dependencies and resource saturation.",
    ),
)
