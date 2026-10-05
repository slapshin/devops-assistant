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
    ),
    Rule(
        "server_error_ratio",
        F.REQUEST_FAILURES,
        "Server error rate (5xx)",
        thresholds="server_error_ratio",
        volume_guard=True,
    ),
    Rule(
        "not_found_rate",
        F.CLIENT_ERRORS,
        "404 responses",
        thresholds="not_found_rate",
        severity_cap=Severity.MEDIUM,
        title_up="404 increase",
    ),
    Rule(
        "client_error_rate",
        F.CLIENT_ERRORS,
        "Client errors (4xx excl. 404)",
        thresholds="client_error_rate",
        severity_cap=Severity.MEDIUM,
        title_up="Client error increase (4xx, excl. 404)",
    ),
    Rule(
        "latency_p95",
        F.LATENCY,
        "p95 latency",
        thresholds="latency_quantile",
        volume_guard=True,
    ),
    Rule(
        "latency_mean",
        F.LATENCY,
        "Mean latency",
        thresholds="latency_quantile",
        volume_guard=True,
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
    ),
    Rule(
        "rpc_error_ratio",
        F.REQUEST_FAILURES,
        "RPC failure rate (status != OK)",
        thresholds="rpc_error_ratio",
        volume_guard=True,
    ),
    Rule(
        "rpc_latency_p95",
        F.LATENCY,
        "RPC p95 latency",
        thresholds="latency_quantile",
        volume_guard=True,
    ),
)

REQUEST_RULES = HTTP_RULES + RPC_RULES
