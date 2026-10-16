"""Request rules: HTTP and RPC traffic, failures, client errors and latency."""

from app.analysis.rules.base import Dir, Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
HTTP_RULES = (
    Rule(
        "request_rate",
        F.REQUEST_TRAFFIC,
        "Request rate",
        thresholds="request_rate",
        direction=Dir.BOTH,
        title_up="Traffic increase",
        title_down="Traffic drop",
        summary="Request rate moved outside its usual range.",
        help=(
            "A drop can mean an outage upstream, broken clients or routing; a rise a "
            "traffic surge, retries or bots. Check error rates and the edge in the same "
            "window."
        ),
    ),
    Rule(
        "server_error_ratio",
        F.REQUEST_FAILURES,
        "Server error rate (5xx)",
        thresholds="server_error_ratio",
        volume_guard=True,
        summary="A larger share of requests failed with 5xx server errors.",
        help=(
            "Check application logs and recent deploys for the affected routes, and whether "
            "a dependency (database, upstream) failed at the same time."
        ),
    ),
    Rule(
        "not_found_rate",
        F.CLIENT_ERRORS,
        "404 responses",
        thresholds="not_found_rate",
        severity_cap=Severity.MEDIUM,
        title_up="404 increase",
        summary="More requests returned 404 Not Found.",
        help=(
            "Look for broken links or a removed route after a deploy, clients using an old "
            "API, or scanners probing for paths."
        ),
    ),
    Rule(
        "client_error_rate",
        F.CLIENT_ERRORS,
        "Client errors (4xx excl. 404)",
        thresholds="client_error_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Client error increase (4xx, excl. 404)",
        summary="More requests were rejected with 4xx client errors (other than 404).",
        help=(
            "Group by status: 401/403 point to auth or token problems, 400/422 to client or "
            "contract changes, 429 to rate limiting."
        ),
    ),
    Rule(
        "latency_p95",
        F.LATENCY,
        "p95 latency",
        thresholds="latency_quantile",
        volume_guard=True,
        summary="The slowest 5 % of requests got slower.",
        help=(
            "Check slow dependencies (database, upstreams), resource saturation and recent "
            "deploys; compare the affected routes with the overall latency."
        ),
    ),
    Rule(
        "latency_mean",
        F.LATENCY,
        "Mean latency",
        thresholds="latency_quantile",
        volume_guard=True,
        summary="Average request duration rose above its usual range.",
        help=(
            "Check slow dependencies and resource saturation; the mean hides outliers, so "
            "add a histogram for percentiles if possible."
        ),
    ),
)

RPC_RULES = (
    Rule(
        "rpc_request_rate",
        F.REQUEST_TRAFFIC,
        "RPC call rate",
        thresholds="request_rate",
        direction=Dir.BOTH,
        title_up="RPC traffic increase",
        title_down="RPC traffic drop",
        summary="RPC call rate moved outside its usual range.",
        help=(
            "A drop can mean callers failing or routing problems; a rise retries or new "
            "load. Check the failure rate of the same service."
        ),
    ),
    Rule(
        "rpc_error_ratio",
        F.REQUEST_FAILURES,
        "RPC failure rate (status != OK)",
        thresholds="rpc_error_ratio",
        volume_guard=True,
        summary="A larger share of RPC calls finished with a non-OK status.",
        help=(
            "Group by status code: UNAVAILABLE and DEADLINE_EXCEEDED point to capacity or "
            "dependencies, INVALID_ARGUMENT to client changes."
        ),
    ),
    Rule(
        "rpc_latency_p95",
        F.LATENCY,
        "RPC p95 latency",
        thresholds="latency_quantile",
        volume_guard=True,
        summary="The slowest 5 % of RPC calls got slower.",
        help=(
            "Check the service's dependencies, saturation and deploys, and whether callers "
            "started hitting deadlines."
        ),
    ),
)

REQUEST_RULES = HTTP_RULES + RPC_RULES
