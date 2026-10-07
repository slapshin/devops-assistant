import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.domain.common import STEP_SECONDS, EntityKind, LabelMatcher, Scope
from app.domain.interfaces import CancellationToken, Cancelled, SourceError, SourceErrorKind
from app.domain.jobs import StageProgress
from app.domain.metrics import CapabilityStatus
from app.domain.projects import PrometheusConnection
from app.domain.report import AnalysisWindows
from app.sources.base import ClientLimits
from app.sources.prometheus.catalog import BY_SIGNAL, CATALOG
from app.sources.prometheus.client import PrometheusClient
from app.sources.prometheus.promql import (
    QueryTemplate,
    ScopeViolation,
    assert_scoped,
    escape_label_value,
    scope_matchers,
)
from app.sources.prometheus.source import (
    CollectionBudget,
    PrometheusMetricsSource,
    _to_grid,
    entity_for,
)
from tests.helpers import make_scope

SCOPE = make_scope()
WEIRD = make_scope('pa"as\\x', "prod\nuction")
T = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
WINDOWS = AnalysisWindows.for_end(T)


class Progress:
    def __init__(self) -> None:
        self.updates: list[StageProgress] = []

    async def update(self, progress: StageProgress) -> None:
        self.updates.append(progress)


Handler = Callable[[str, dict[str, list[str]]], httpx.Response]


class FakeProm:
    """Minimal Prometheus API: routes requests to a handler and records them."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[tuple[str, dict[str, list[str]]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params: dict[str, list[str]] = {}
        for k, v in request.url.params.multi_items():
            params.setdefault(k, []).append(v)
        self.requests.append((request.url.path, params))
        return self.handler(request.url.path, params)

    def client(self, **limits: Any) -> PrometheusClient:
        return PrometheusClient(
            "http://metrics.test/prefix",
            transport=httpx.MockTransport(self),
            limits=ClientLimits(**limits),
        )


def ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": data})


def matrix(*series: tuple[dict[str, str], list[tuple[int, str]]]) -> httpx.Response:
    return ok(
        {
            "resultType": "matrix",
            "result": [{"metric": m, "values": [[t, v] for t, v in vals]} for m, vals in series],
        }
    )


def vector(*series: tuple[dict[str, str], str]) -> httpx.Response:
    return ok(
        {
            "resultType": "vector",
            "result": [{"metric": m, "value": [int(T.timestamp()), v]} for m, v in series],
        }
    )


# --- scoping -----------------------------------------------------------------------------


def test_label_values_are_escaped() -> None:
    assert escape_label_value('a"b\\c\nd') == 'a\\"b\\\\c\\nd'
    assert scope_matchers(WEIRD) == 'env="prod\\nuction", project="pa\\"as\\\\x"'


ONE = Scope(project_id="p", project_name="p", matchers=[LabelMatcher(name="team", value="a")])
THREE = Scope(
    project_id="p",
    project_name="p",
    matchers=[
        LabelMatcher(name="project", value='q"u\\o"te'),
        LabelMatcher(name="env", value="prod"),
        LabelMatcher(name="cluster", value="eu,1}"),
    ],
)


@pytest.mark.parametrize("defn", CATALOG, ids=lambda d: d.signal)
@pytest.mark.parametrize(
    "scope", [SCOPE, WEIRD, ONE, THREE], ids=["plain", "escaped", "one", "three"]
)
def test_every_catalog_selector_and_gate_is_scoped(defn: Any, scope: Scope) -> None:
    rendered = [defn.query.render(scope)] + [g.template.render(scope) for g in defn.gates]

    for query in rendered:
        blocks = re.findall(r"\{[^{}]*\}", re.sub(r'"(?:[^"\\]|\\.)*"', '""', query))
        assert blocks, query
        assert query.count(scope_matchers(scope)) >= len(blocks)


def test_unscoped_ratio_operand_is_rejected() -> None:
    bad = QueryTemplate('a{{{s}}} / b{{job="x"}}', ("a", "b"))

    with pytest.raises(ScopeViolation, match="not scoped"):
        bad.render(SCOPE)


def test_bare_metric_name_is_rejected() -> None:
    with pytest.raises(ScopeViolation, match="without a selector"):
        QueryTemplate("rate(a{{{s}}}[5m]) / b", ("a", "b")).render(SCOPE)


def test_string_literal_cannot_fake_scope() -> None:
    query = 'label_replace(up{job="x"}, "l", "{project=\\"shop\\", env=\\"production\\"}", "", "")'

    with pytest.raises(ScopeViolation):
        assert_scoped(query, SCOPE, ("up",))


def test_other_project_matcher_does_not_satisfy_scope() -> None:
    with pytest.raises(ScopeViolation):
        assert_scoped('up{project="shop-gpu", env="production"}', SCOPE, ("up",))


def test_every_matcher_is_required() -> None:
    full = f"up{{{scope_matchers(THREE)}}}"

    assert_scoped(full, THREE, ("up",))

    for dropped in THREE.matchers:
        rest = [m for m in THREE.matchers if m != dropped]
        partial = "up{" + ", ".join(f'{m.name}="{escape_label_value(m.value)}"' for m in rest) + "}"
        with pytest.raises(ScopeViolation):
            assert_scoped(partial, THREE, ("up",))


COUNTER = re.compile(r"\b([a-z_:]+_(?:total|count|sum|bucket))\{")


@pytest.mark.parametrize("defn", CATALOG, ids=lambda d: d.signal)
def test_counters_are_rated_before_aggregation(defn: Any) -> None:
    query = defn.query.render(SCOPE)

    for m in COUNTER.finditer(query):
        if m.group(1) == "docker_swarm_task_info":
            continue
        head = query[: m.start()].rstrip()
        assert head.endswith(("rate(", "increase(")), f"{m.group(1)} not rated first: {query}"

    for m in re.finditer(r'\{__name__=~"[^"]*_total"', query):
        assert query[: m.start()].rstrip().endswith(("rate(", "increase(")), query


# Proxy, PostgreSQL and MySQL counters exported without a conventional suffix.
UNSUFFIXED_COUNTERS = (
    "angie_http_server_zones_responses",
    "angie_connections_dropped",
    "nginx_connections_accepted",
    "nginx_connections_handled",
    "pg_stat_database_xact_commit",
    "pg_stat_database_xact_rollback",
    "pg_stat_database_deadlocks",
    "pg_stat_database_temp_bytes",
    "mysql_global_status_questions",
    "mysql_global_status_slow_queries",
    "mysql_global_status_innodb_row_lock_waits",
    "mysql_global_status_created_tmp_disk_tables",
)


@pytest.mark.parametrize("defn", CATALOG, ids=lambda d: d.signal)
def test_unsuffixed_counters_are_rated_before_aggregation(defn: Any) -> None:
    query = defn.query.render(SCOPE)

    for name in UNSUFFIXED_COUNTERS:
        for m in re.finditer(rf"\b{name}\{{", query):
            prefix = query[: m.start()].rstrip()
            assert prefix.endswith(("rate(", "increase(")), f"{name} not rated: {query}"


def test_postgres_database_signals_skip_template_databases() -> None:
    for defn in CATALOG:
        if "datname" in defn.identity:
            query = defn.query.render(SCOPE)
            selectors = re.findall(r"pg_\w+\{[^}]*\}", query)
            assert selectors and all('datname!~"|template[01]"' in s for s in selectors), query


def test_postgres_entity_names() -> None:
    server = entity_for(BY_SIGNAL["pg_down"], {"job": "postgres", "instance": "db1:9187"})
    database = entity_for(
        BY_SIGNAL["pg_transactions"], {"job": "postgres", "instance": "db1:9187", "datname": "app"}
    )

    assert server.display_name == "postgres · db1:9187"
    assert database.display_name == "postgres · db1:9187 · app"
    assert server.kind is database.kind is EntityKind.DATABASE


def test_mysql_replication_signals_cover_both_status_syntaxes() -> None:
    assert BY_SIGNAL["mysql_slave_lag"].required_metrics == (
        "mysql_slave_status_seconds_behind_master",
    )
    assert BY_SIGNAL["mysql_replica_lag"].required_metrics == (
        "mysql_slave_status_seconds_behind_source",
    )

    stopped = BY_SIGNAL["mysql_replica_stopped"].query.render(SCOPE)
    assert "mysql_slave_status_replica_sql_running{" in stopped
    assert "mysql_slave_status_replica_io_running{" in stopped

    refused = BY_SIGNAL["mysql_connections_refused"].query.render(SCOPE)
    assert 'error="max_connections"' in refused


def test_mysql_entity_names() -> None:
    server = entity_for(BY_SIGNAL["mysql_down"], {"job": "mysql", "instance": "db1:9104"})

    assert server.display_name == "mysql · db1:9104"
    assert server.kind is EntityKind.DATABASE


def test_redis_ratios_need_a_configured_limit() -> None:
    clients = BY_SIGNAL["redis_clients_used_ratio"]
    query = clients.query.render(SCOPE)

    assert "redis_max_clients{" in query and "redis_config_maxclients{" in query  # INFO or CONFIG
    assert clients.required_metrics == ("redis_connected_clients",)
    assert [g.template.render(SCOPE) for g in clients.gates]

    memory = BY_SIGNAL["redis_memory_used_ratio"]

    assert "redis_memory_max_bytes{" in memory.gates[0].template.render(SCOPE)
    assert "> 0" in memory.query.render(SCOPE)  # maxmemory 0 means unlimited


def test_redis_entity_names() -> None:
    server = entity_for(
        BY_SIGNAL["redis_down"], {"job": "redis", "instance": "redis://cache1:6379"}
    )

    assert server.display_name == "redis · redis://cache1:6379"
    assert server.kind is EntityKind.DATABASE


def test_proxy_entity_names() -> None:
    peer = entity_for(
        BY_SIGNAL["angie_peer_unavailable"],
        {"job": "angie", "upstream": "backend", "peer": "10.0.0.2:80"},
    )

    assert peer.display_name == "angie · backend → 10.0.0.2:80"

    caddy = entity_for(
        BY_SIGNAL["caddy_requests"], {"job": "caddy", "server": "srv0", "handler": "reverse_proxy"}
    )

    assert caddy.display_name == "caddy · srv0 · reverse_proxy"
    assert "host" not in caddy.labels  # absent optional label: unchanged key and labels
    assert caddy.key != entity_for(BY_SIGNAL["traefik_requests"], {"job": "caddy"}).key

    per_host = entity_for(
        BY_SIGNAL["caddy_requests"],
        {"job": "caddy", "host": "shop.example.com", "server": "srv0", "handler": "rp"},
    )

    assert per_host.display_name == "caddy · shop.example.com · srv0 · rp"
    assert per_host.key != caddy.key

    router = entity_for(
        BY_SIGNAL["traefik_5xx"],
        {"job": "traefik", "router": "shop@docker", "service": "shop@docker"},
    )

    assert router.display_name == "traefik · shop@docker"  # same name shown once
    assert router.labels["router"] == "shop@docker"


def test_http_route_names_show_the_served_host() -> None:
    labels = {"job": "api", "http_route": "/orders", "http_request_method": "GET"}

    plain = entity_for(BY_SIGNAL["http_requests"], labels)

    assert plain.display_name == "api · GET /orders"
    assert "server_address" not in plain.labels

    hosted = entity_for(BY_SIGNAL["http_requests"], {**labels, "server_address": "shop.example"})

    assert hosted.display_name == "api · GET /orders @ shop.example"


def test_optional_host_labels_are_not_required() -> None:
    for signal, label in (
        ("caddy_requests", "host"),
        ("caddy_latency_p95", "host"),
        ("traefik_requests", "router"),
        ("traefik_latency_p99", "router"),
        ("http_requests", "server_address"),
    ):
        defn = BY_SIGNAL[signal]
        assert label in defn.identity and label not in defn.required_labels, signal


def test_traefik_prefers_router_series_per_service() -> None:
    for signal in ("traefik_requests", "traefik_5xx", "traefik_latency_p95"):
        defn = BY_SIGNAL[signal]
        query = defn.query.render(SCOPE)
        assert "traefik_router_" in query and "traefik_service_" in query, signal
        # Presence is tested on the unfiltered router counter, never on the status-filtered one.
        assert "unless on (job, service) rate(traefik_router_requests_total{" in query
        assert all(m.startswith("traefik_service_") for m in defn.required_metrics), signal


def test_histogram_quantiles_keep_le_and_are_gated() -> None:
    for defn in CATALOG:
        if defn.signal.endswith(("_p95", "_p99")):
            query = defn.query.render(SCOPE)
            assert re.search(r"sum by \([^)]*\ble\)", query), query
            assert defn.gates, defn.signal


def test_container_restart_is_not_a_cadvisor_counter() -> None:
    assert "container_start_time_seconds" not in json.dumps([d.required_metrics for d in CATALOG])
    assert BY_SIGNAL["swarm_failed_tasks"].required_metrics == ("docker_swarm_task_info",)


# --- client -------------------------------------------------------------------------------


async def test_range_queries_are_chunked_contiguously_and_merged() -> None:
    step = STEP_SECONDS

    def handler(path: str, params: dict[str, list[str]]) -> httpx.Response:
        start, end = int(params["start"][0]), int(params["end"][0])
        return matrix(({"job": "node"}, [(start, "1"), (end, "NaN")]))

    fake = FakeProm(handler)
    client = fake.client()
    start = int(T.timestamp()) - 28 * 86400 + step

    res = await client.query_range("up{x}", start, int(T.timestamp()), step)

    windows = [(int(p["start"][0]), int(p["end"][0])) for _, p in fake.requests]
    assert len(windows) == 4
    assert windows[0][0] == start and windows[-1][1] == int(T.timestamp())
    for (_, end), (nxt, _) in pairwise(windows):
        assert nxt == end + step
    assert all(path == "/prefix/api/v1/query_range" for path, _ in fake.requests)

    assert len(res) == 1 and len(res[0].samples) == 8
    assert res[0].samples[1][1] is None  # NaN -> gap


async def test_transient_failure_is_retried_once_then_reported() -> None:
    calls = {"n": 0}

    def handler(path: str, params: dict[str, list[str]]) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"status": "error", "error": "overloaded"})

    client = FakeProm(handler).client()

    with pytest.raises(SourceError) as exc:
        await client.query("up", 1)

    assert exc.value.kind is SourceErrorKind.UNAVAILABLE and calls["n"] == 2


async def test_bad_query_and_timeouts_are_not_retried() -> None:
    responses = iter(
        [
            httpx.Response(422, json={"status": "error", "error": "parse error"}),
            httpx.Response(503, json={"status": "error", "error": "deadline exceeded after 30s"}),
        ]
    )
    fake = FakeProm(lambda path, params: next(responses))
    client = fake.client()

    with pytest.raises(SourceError) as bad:
        await client.query("up{", 1)
    with pytest.raises(SourceError) as slow:
        await client.query("up", 2)

    assert bad.value.kind is SourceErrorKind.BAD_QUERY
    assert slow.value.kind is SourceErrorKind.TIMEOUT
    assert len(fake.requests) == 2


async def test_unreachable_source_and_auth_errors() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = PrometheusClient("http://x", transport=httpx.MockTransport(refuse))

    with pytest.raises(SourceError) as exc:
        await client.query("up", 1)

    assert exc.value.kind is SourceErrorKind.UNAVAILABLE

    denied = FakeProm(lambda p, q: httpx.Response(401, text="no")).client()

    with pytest.raises(SourceError) as auth:
        await denied.query("up", 1)

    assert auth.value.kind is SourceErrorKind.AUTH


async def test_response_size_limit() -> None:
    big = FakeProm(lambda p, q: ok({"result": [{"metric": {}, "value": [1, "1" * 5000]}]}))

    with pytest.raises(SourceError) as exc:
        await big.client(max_response_bytes=1000).query("up", 1)

    assert exc.value.kind is SourceErrorKind.TOO_LARGE


async def test_cache_is_keyed_by_exact_query() -> None:
    fake = FakeProm(lambda p, q: vector(({"a": "1"}, "1")))
    client = fake.client()
    shop = f"up{{{scope_matchers(SCOPE)}}}"
    other = f"up{{{scope_matchers(make_scope('shop-gpu', 'production'))}}}"

    await client.query(shop, 10)
    await client.query(shop, 10)
    await client.query(other, 10)

    assert [p["query"][0] for _, p in fake.requests] == [shop, other]


async def test_bearer_token_is_sent_but_never_in_query() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok({"result": []})

    conn = PrometheusConnection(url="http://vm.example", bearer_token=SecretStr("tok-123"))
    client = PrometheusClient.from_connection(conn, transport=httpx.MockTransport(record))

    await client.query("up", 1)

    assert seen[0].headers["Authorization"] == "Bearer tok-123"
    assert "tok-123" not in str(seen[0].url)


# --- source -------------------------------------------------------------------------------


def test_grid_places_interval_samples_and_keeps_gaps() -> None:
    from app.sources.prometheus.client import RangeResult

    start, step = 1000 * STEP_SECONDS, STEP_SECONDS
    r = RangeResult(labels={}, samples=[(start + step, 1.0), (start + 3 * step, 3.0), (7, 9.0)])

    assert _to_grid(r, start, step, 4) == [1.0, None, 3.0, None]


def present_handler(present: set[str], extra: dict[str, httpx.Response] | None = None) -> Handler:
    def handler(path: str, params: dict[str, list[str]]) -> httpx.Response:
        query = params.get("query", [""])[0]
        if query.startswith("count by (__name__)"):
            return vector(*[({"__name__": m}, "1") for m in sorted(present)])
        for needle, response in (extra or {}).items():
            if needle in query and (
                path.endswith("/query_range") or needle.endswith(("info", "seconds", "{"))
            ):
                return response
        if path.endswith("/query_range"):
            if query.startswith("count(up"):
                return matrix(({}, [(int(T.timestamp()) - 20 * 86400, "4")]))
            return matrix()
        if query.startswith("count(") and "> 0" in query:
            return vector()
        if "count by (instance) (container_cpu_usage_seconds_total" in query:
            if 'name!=""' in query:
                return vector(({"instance": "h1"}, "3"))
            return vector(({"instance": "h1"}, "4"), ({"instance": "h2"}, "1"))
        return vector(({}, "1"))

    return handler


NODE_ONLY = {"node_cpu_seconds_total", "http_server_request_duration_seconds_count"}


async def test_capabilities_gate_on_presence_and_limits() -> None:
    present = NODE_ONLY | {
        "container_cpu_usage_seconds_total",
        "container_memory_working_set_bytes",
        "container_spec_memory_limit_bytes",
        "http_server_request_duration_seconds_sum",
    }
    source = PrometheusMetricsSource(FakeProm(present_handler(present)).client(), now=lambda: T)

    caps = {c.signal: c for c in await source.capabilities(SCOPE, WINDOWS)}

    assert caps["cpu_utilization"].status is CapabilityStatus.SUPPORTED
    assert caps["cpu_utilization"].history_days == 20.0
    assert caps["memory_utilization"].status is CapabilityStatus.UNSUPPORTED
    assert "node_memory_MemAvailable_bytes" in (caps["memory_utilization"].reason or "")
    assert caps["http_latency_p95"].status is CapabilityStatus.UNSUPPORTED
    assert "_bucket" in (caps["http_latency_p95"].reason or "")
    assert caps["http_latency_mean"].status is CapabilityStatus.SUPPORTED
    assert caps["container_memory_limit_ratio"].status is CapabilityStatus.UNSUPPORTED
    assert caps["container_cpu"].status is CapabilityStatus.PARTIAL
    assert "h2" in (caps["container_cpu"].reason or "")
    assert all(c.verified for c in caps.values())


def route_series(n_routes: int) -> httpx.Response:
    t0 = int(T.timestamp()) - 28 * 86400 + STEP_SECONDS
    return matrix(
        *[
            (
                {"job": "api", "http_route": f"/r{i}", "http_request_method": "GET"},
                [(t0, str(i)), (int(T.timestamp()), str(i))],
            )
            for i in range(n_routes)
        ]
    )


async def test_collect_limits_routes_and_aggregates_the_rest() -> None:
    handler = present_handler(
        NODE_ONLY, {"http_server_request_duration_seconds_count": route_series(5)}
    )
    source = PrometheusMetricsSource(
        FakeProm(handler).client(), CollectionBudget(top_routes_per_service=2), now=lambda: T
    )

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    reqs = [s for s in result.series if s.signal == "http_requests"]
    routes = sorted(s.entity.labels["http_route"] for s in reqs)
    assert routes == ["(other routes)", "/r3", "/r4"]
    other = next(s for s in reqs if s.entity.labels["http_route"] == "(other routes)")
    assert other.values[-1] == 0 + 1 + 2
    assert all(s.labels["project"] == "shop" for s in result.series)
    assert all(len(s.values) == 28 * 288 for s in result.series)


async def test_proxy_services_are_ranked_by_their_own_traffic() -> None:
    t0 = int(T.timestamp()) - 28 * 86400 + STEP_SECONDS
    services = matrix(
        *[
            (
                {"job": "traefik", "service": f"s{i}@docker"},
                [(t0, str(i)), (int(T.timestamp()), str(i))],
            )
            for i in range(5)
        ]
    )
    handler = present_handler(
        NODE_ONLY | {"traefik_service_requests_total"},
        {"traefik_service_requests_total": services},
    )
    source = PrometheusMetricsSource(
        FakeProm(handler).client(), CollectionBudget(top_routes_per_service=2), now=lambda: T
    )

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    for signal in ("traefik_requests", "traefik_5xx", "traefik_404"):
        kept = sorted(s.entity.labels["service"] for s in result.series if s.signal == signal)
        assert kept == ["(other routes)", "s3@docker", "s4@docker"], signal

    other = next(
        s
        for s in result.series
        if s.signal == "traefik_requests" and s.entity.labels["service"] == "(other routes)"
    )
    assert other.values[-1] == 0 + 1 + 2
    assert other.entity.display_name == "traefik · (other routes)"


async def test_traefik_router_series_are_ranked_and_rolled_up() -> None:
    t0 = int(T.timestamp()) - 28 * 86400 + STEP_SECONDS
    routers = matrix(
        *[
            (
                {"job": "traefik", "router": f"r{i}@docker", "service": "shop@docker"},
                [(t0, str(i)), (int(T.timestamp()), str(i))],
            )
            for i in range(4)
        ]
    )
    handler = present_handler(
        NODE_ONLY | {"traefik_service_requests_total"},
        {"traefik_router_requests_total": routers},
    )
    source = PrometheusMetricsSource(
        FakeProm(handler).client(), CollectionBudget(top_routes_per_service=2), now=lambda: T
    )

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    names = sorted(s.entity.display_name for s in result.series if s.signal == "traefik_requests")
    assert names == [
        "traefik · (other routes)",
        "traefik · r2@docker · shop@docker",
        "traefik · r3@docker · shop@docker",
    ]


async def test_failing_signal_becomes_exclusion_not_crash() -> None:
    boom = httpx.Response(422, json={"status": "error", "error": "bad"})
    handler = present_handler(NODE_ONLY, {'mode="iowait"': boom})
    source = PrometheusMetricsSource(FakeProm(handler).client(), now=lambda: T)

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    assert [(e.code, e.family) for e in result.exclusions][:1] == [("query_failed", "cpu")]
    assert "cpu_iowait" in result.exclusions[0].message


async def test_per_query_series_budget_is_disclosed() -> None:
    handler = present_handler(
        NODE_ONLY, {"http_server_request_duration_seconds_count": route_series(8)}
    )
    source = PrometheusMetricsSource(
        FakeProm(handler).client(), CollectionBudget(max_series_per_query=3), now=lambda: T
    )

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    assert any(e.code == "series_truncated" for e in result.exclusions)


async def test_out_of_scope_results_are_dropped() -> None:
    leaked = matrix(
        (
            {"job": "node", "instance": "a", "project": "shop", "env": "production"},
            [(int(T.timestamp()), "0.5")],
        ),
        (
            {"job": "node", "instance": "b", "project": "shop-gpu", "env": "production"},
            [(int(T.timestamp()), "0.9")],
        ),
    )
    handler = present_handler(NODE_ONLY, {'mode="idle"': leaked})
    source = PrometheusMetricsSource(FakeProm(handler).client(), now=lambda: T)

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    cpu = [s for s in result.series if s.signal == "cpu_utilization"]
    assert [s.entity.labels["instance"] for s in cpu] == ["a"]


async def test_cancellation_stops_collection() -> None:
    fake = FakeProm(present_handler(NODE_ONLY))
    source = PrometheusMetricsSource(fake.client(), now=lambda: T)
    caps = await source.capabilities(SCOPE, WINDOWS)
    token = CancellationToken()
    token.cancel()
    before = len(fake.requests)

    with pytest.raises(Cancelled):
        await source.collect(SCOPE, WINDOWS, caps, Progress(), token)

    assert len(fake.requests) == before


async def test_service_to_host_mapping_by_container_id_prefix() -> None:
    extra = {
        "target_info": vector(
            ({"job": "checkout-api", "service_instance_id": "1711aba3a574"}, "1")
        ),
        "container_start_time_seconds": vector(
            (
                {
                    "instance": "shop-production-2",
                    "id": "/system.slice/docker-1711aba3a574ff6224911dc2.scope",
                },
                "1",
            )
        ),
        "docker_swarm_task_info{": vector(
            ({"service_name": "shop_checkout-api", "node_hostname": "shop-production-2"}, "1")
        ),
    }
    source = PrometheusMetricsSource(
        FakeProm(present_handler(NODE_ONLY, extra)).client(), now=lambda: T
    )

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    assert [(m.kind.value, m.service, m.host) for m in result.mappings] == [
        ("otel_service", "checkout-api", "shop-production-2"),
        ("swarm_service", "shop_checkout-api", "shop-production-2"),
    ]
