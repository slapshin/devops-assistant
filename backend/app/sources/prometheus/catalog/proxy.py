"""Reverse proxy signals: nginx, Angie, Caddy and Traefik."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    Direction,
    RateQuery,
    Role,
    SignalDef,
    aggregate,
    latency_quantiles,
    q,
    rate,
    sel,
    sum_rate,
)

# nginx: nginx-prometheus-exporter over stub_status (no status codes, no latency).
# Angie: the built-in prometheus module with the stock prometheus_all.conf template.
# Caddy: the built-in metrics endpoint; request counts come from the duration histogram, which
#   is the only request metric carrying `code`. `host` is set with `metrics { per_host }`.
# Traefik: the built-in Prometheus exporter with service labels (the default). Where router labels
#   are enabled too (`addRoutersLabels`), a service's router series replace its service series:
#   routers are Traefik's analog of virtual hosts. Services without router series stay as-is.
# Host-like labels an exporter may not emit are optional: absent, they are empty and unnamed.
NGINX = ("job", "instance")
ANGIE = ("job", "instance")
ANGIE_ZONE = ("job", "zone")
ANGIE_PEER = ("job", "upstream", "peer")
CADDY = ("job", "host", "server", "handler")
CADDY_UPSTREAM = ("job", "upstream")
TRAEFIK = ("job", "router", "service")
TRAEFIK_ENTRYPOINT = ("job", "entrypoint")
TRAEFIK_SERVER = ("job", "service", "url")

ANGIE_TRAFFIC = "angie_requests"
CADDY_TRAFFIC = "caddy_requests"
TRAEFIK_TRAFFIC = "traefik_requests"

_ANGIE_REQUESTS = "angie_http_server_zones_requests_total"
_ANGIE_RESPONSES = "angie_http_server_zones_responses"
_ANGIE_PEER_STATE = "angie_http_upstreams_peers_state"
_CADDY_COUNT = "caddy_http_request_duration_seconds_count"
_CADDY_BUCKET = "caddy_http_request_duration_seconds_bucket"
_TRAEFIK_COUNT = "traefik_service_requests_total"
_TRAEFIK_BUCKET = "traefik_service_request_duration_seconds_bucket"
_TRAEFIK_ROUTER_COUNT = "traefik_router_requests_total"
_TRAEFIK_ROUTER_BUCKET = "traefik_router_request_duration_seconds_bucket"
_TRAEFIK_ROUTER_METRIC = {
    _TRAEFIK_COUNT: _TRAEFIK_ROUTER_COUNT,
    _TRAEFIK_BUCKET: _TRAEFIK_ROUTER_BUCKET,
}

# Angie peer states (prometheus_all.conf): 3 = unavailable (max_fails reached), 5 = unhealthy
# (failed active health check). down (2) is configured by the operator, so it is not a failure.
_ANGIE_PEER_FAILED_STATES = (3, 5)


def _traefik_sum_rate(metric: str, by: tuple[str, ...], *matchers: str) -> str:
    """Router series for services that have any, service series for the rest.

    Presence is tested on the unfiltered router counter, so a service never mixes router and
    service series when only some of its status codes have samples in a window.
    """
    routers = sum_rate(_TRAEFIK_ROUTER_METRIC[metric], by, *matchers)
    services = sum_rate(metric, tuple(k for k in by if k != "router"), *matchers)
    return f"({routers}) or ({services} unless on (job, service) {rate(_TRAEFIK_ROUTER_COUNT)})"


def _proxy_request_signals(
    prefix: str,
    traffic: str,
    identity: tuple[str, ...],
    total: str,
    responses: str,
    *,
    optional: tuple[str, ...] = (),
    by_entity: RateQuery = sum_rate,
    extra_metrics: tuple[str, ...] = (),
) -> tuple[SignalDef, ...]:
    """Request rate plus 5xx/4xx/404 operands for one proxy, all on the same identity."""

    def status(signal: str, family: SignalFamily, matcher: str, role: Role) -> SignalDef:
        return SignalDef(
            f"{prefix}_{signal}",
            family,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.PROXY,
            identity,
            q(by_entity(responses, identity, matcher), responses, *extra_metrics),
            required_metrics=(responses,),
            optional_labels=optional,
            role=role,
            traffic=traffic,
        )

    return (
        SignalDef(
            traffic,
            SignalFamily.REQUEST_TRAFFIC,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.PROXY,
            identity,
            q(by_entity(total, identity), total, *extra_metrics),
            required_metrics=(total,),
            optional_labels=optional,
            direction=Direction.BOTH,
            traffic=traffic,
        ),
        status("5xx", SignalFamily.REQUEST_FAILURES, 'code=~"5.."', Role.OPERAND),
        status("4xx", SignalFamily.CLIENT_ERRORS, 'code=~"4.."', Role.SIGNAL),
        status("404", SignalFamily.CLIENT_ERRORS, 'code="404"', Role.SIGNAL),
    )


def _active_connections(prefix: str, metric: str, identity: tuple[str, ...]) -> SignalDef:
    return SignalDef(
        f"{prefix}_connections_active",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.PROXY,
        identity,
        q(aggregate("sum", identity, sel(metric)), metric),
        description="Open client connections.",
    )


PROXY_CATALOG: tuple[SignalDef, ...] = (
    # --- nginx (stub_status) --------------------------------------------------------------
    SignalDef(
        "nginx_requests",
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.PROXY,
        NGINX,
        q(sum_rate("nginx_http_requests_total", NGINX), "nginx_http_requests_total"),
        direction=Direction.BOTH,
        description="All requests; stub_status has no status codes or latency.",
    ),
    _active_connections("nginx", "nginx_connections_active", NGINX),
    SignalDef(
        "nginx_connections_dropped",
        SignalFamily.PROXY,
        Unit.PER_SECOND,
        EntityKind.PROXY,
        NGINX,
        q(
            f"{sum_rate('nginx_connections_accepted', NGINX)}"
            f" - {sum_rate('nginx_connections_handled', NGINX)}",
            "nginx_connections_accepted",
            "nginx_connections_handled",
        ),
        description="Accepted minus handled connections (worker_connections or fd limits).",
    ),
    SignalDef(
        "nginx_down",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.PROXY,
        NGINX,
        q("1 - " + aggregate("max", NGINX, sel("nginx_up")), "nginx_up"),
        description="1 while the exporter cannot read stub_status.",
    ),
    # --- Angie (prometheus_all.conf) ------------------------------------------------------
    *_proxy_request_signals("angie", ANGIE_TRAFFIC, ANGIE_ZONE, _ANGIE_REQUESTS, _ANGIE_RESPONSES),
    _active_connections("angie", "angie_connections_active", ANGIE),
    SignalDef(
        "angie_connections_dropped",
        SignalFamily.PROXY,
        Unit.PER_SECOND,
        EntityKind.PROXY,
        ANGIE,
        q(sum_rate("angie_connections_dropped", ANGIE), "angie_connections_dropped"),
    ),
    SignalDef(
        "angie_peer_unavailable",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        ANGIE_PEER,
        q(
            aggregate(
                "max",
                ANGIE_PEER,
                " + ".join(
                    f"({sel(_ANGIE_PEER_STATE)} == bool {state})"
                    for state in _ANGIE_PEER_FAILED_STATES
                ),
            ),
            _ANGIE_PEER_STATE,
        ),
        description="1 while the peer is unavailable or unhealthy.",
    ),
    # --- Caddy ----------------------------------------------------------------------------
    *_proxy_request_signals(
        "caddy", CADDY_TRAFFIC, CADDY, _CADDY_COUNT, _CADDY_COUNT, optional=("host",)
    ),
    *latency_quantiles(
        "caddy", EntityKind.PROXY, CADDY, _CADDY_BUCKET, CADDY_TRAFFIC, optional=("host",)
    ),
    SignalDef(
        "caddy_upstream_unhealthy",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        CADDY_UPSTREAM,
        q(
            "1 - " + aggregate("min", CADDY_UPSTREAM, sel("caddy_reverse_proxy_upstreams_healthy")),
            "caddy_reverse_proxy_upstreams_healthy",
        ),
        description="1 while any Caddy instance reports the upstream unhealthy.",
    ),
    # --- Traefik --------------------------------------------------------------------------
    *_proxy_request_signals(
        "traefik",
        TRAEFIK_TRAFFIC,
        TRAEFIK,
        _TRAEFIK_COUNT,
        _TRAEFIK_COUNT,
        optional=("router",),
        by_entity=_traefik_sum_rate,
        extra_metrics=(_TRAEFIK_ROUTER_COUNT,),
    ),
    *latency_quantiles(
        "traefik",
        EntityKind.PROXY,
        TRAEFIK,
        _TRAEFIK_BUCKET,
        TRAEFIK_TRAFFIC,
        optional=("router",),
        by_entity=_traefik_sum_rate,
        extra_metrics=(_TRAEFIK_ROUTER_BUCKET, _TRAEFIK_ROUTER_COUNT),
    ),
    _active_connections("traefik", "traefik_open_connections", TRAEFIK_ENTRYPOINT),
    SignalDef(
        "traefik_server_down",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        TRAEFIK_SERVER,
        q(
            "1 - " + aggregate("min", TRAEFIK_SERVER, sel("traefik_service_server_up")),
            "traefik_service_server_up",
        ),
        description="1 while any Traefik instance reports the server down.",
    ),
)
