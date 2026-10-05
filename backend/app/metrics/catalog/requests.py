"""HTTP and RPC server signals (OpenTelemetry semantic conventions)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.metrics.catalog.base import (
    Direction,
    Role,
    SignalDef,
    latency_quantiles,
    q,
    sum_rate,
)

HTTP = ("job", "http_route", "http_request_method")
RPC = ("job", "rpc_method")

HTTP_TRAFFIC = "http_requests"
RPC_TRAFFIC = "rpc_requests"

_HTTP_COUNT = "http_server_request_duration_seconds_count"
_HTTP_BUCKET = "http_server_request_duration_seconds_bucket"
_HTTP_SUM = "http_server_request_duration_seconds_sum"
_RPC_COUNT = "rpc_server_call_duration_seconds_count"
_RPC_BUCKET = "rpc_server_call_duration_seconds_bucket"


def _http_status(signal: str, family: SignalFamily, matcher: str, role: Role) -> SignalDef:
    return SignalDef(
        signal,
        family,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(sum_rate(_HTTP_COUNT, HTTP, matcher), _HTTP_COUNT),
        role=role,
        traffic=HTTP_TRAFFIC,
    )


REQUEST_CATALOG: tuple[SignalDef, ...] = (
    # --- HTTP ------------------------------------------------------------------------------
    SignalDef(
        HTTP_TRAFFIC,
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(sum_rate(_HTTP_COUNT, HTTP), _HTTP_COUNT),
        direction=Direction.BOTH,
        traffic=HTTP_TRAFFIC,
    ),
    _http_status(
        "http_5xx", SignalFamily.REQUEST_FAILURES, 'http_response_status_code=~"5.."', Role.OPERAND
    ),
    _http_status(
        "http_4xx", SignalFamily.CLIENT_ERRORS, 'http_response_status_code=~"4.."', Role.SIGNAL
    ),
    _http_status(
        "http_404", SignalFamily.CLIENT_ERRORS, 'http_response_status_code="404"', Role.SIGNAL
    ),
    *latency_quantiles("http", EntityKind.ROUTE, HTTP, _HTTP_BUCKET, HTTP_TRAFFIC),
    SignalDef(
        "http_latency_mean",
        SignalFamily.LATENCY,
        Unit.SECONDS,
        EntityKind.ROUTE,
        HTTP,
        q(
            f"{sum_rate(_HTTP_SUM, HTTP)} / ({sum_rate(_HTTP_COUNT, HTTP)} > 0)",
            _HTTP_SUM,
            _HTTP_COUNT,
        ),
        traffic=HTTP_TRAFFIC,
        description="Mean latency; used only when histogram buckets are absent.",
    ),
    # --- RPC -------------------------------------------------------------------------------
    SignalDef(
        RPC_TRAFFIC,
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        RPC,
        q(sum_rate(_RPC_COUNT, RPC), _RPC_COUNT),
        direction=Direction.BOTH,
        traffic=RPC_TRAFFIC,
    ),
    SignalDef(
        "rpc_errors",
        SignalFamily.REQUEST_FAILURES,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        RPC,
        q(sum_rate(_RPC_COUNT, RPC, 'rpc_response_status_code!="OK"'), _RPC_COUNT),
        role=Role.OPERAND,
        traffic=RPC_TRAFFIC,
    ),
    *latency_quantiles("rpc", EntityKind.ROUTE, RPC, _RPC_BUCKET, RPC_TRAFFIC, phis=(0.95,)),
)
