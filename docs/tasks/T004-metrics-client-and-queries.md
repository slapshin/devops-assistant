# T004 — Metrics client and scoped query catalog

Dependencies: T002, T003. Status: see [task index](README.md).

## Outcome

The analysis engine receives normalized metric series and explicit capability/coverage information for exactly one project/env.

## Ownership

`backend/app/metrics/`, query configuration, and metrics tests.

## Work

- Implement bounded query-range and metadata discovery calls with timeouts, limited retries/concurrency, cancellation, and response-size limits. The standard endpoints and response formats are described by the [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).
- Enforce escaped exact project/env matchers on every metric selector, including both operands of ratios and discovery queries where the selected scope is known.
- Build a versioned catalog for CPU busy/iowait, available memory, swap/pressure where exposed, filesystem capacity/inodes, disk I/O, network errors/drops, container CPU/memory/throttling, HTTP/RPC traffic/errors/latency.
- Apply rates to individual counters before aggregation; handle resets, absent series, invalid denominators, histogram units, and cumulative buckets correctly.
- Treat restart detection as capability-dependent. cAdvisor alone must not be assumed to provide a trustworthy restart counter; document proxies and their uncertainty if used.
- Exclude pseudo filesystems and duplicate container/system series through documented configurable rules. Aggregate before returning data where possible; report truncation and incomplete coverage explicitly.
- Cache by source, exact scope, query, window, step, and relevant configuration. Never mix project/env data.

## Acceptance

- [x] Every selector is correctly scoped and escaped, including ratio operands and selected-scope discovery.
- [x] Counter rates, aggregation, reset behavior, invalid denominators, histogram units, and missing series are handled correctly.
- [x] Metric capabilities explicitly gate latency, restart, and limit-dependent calculations.
- [x] Timeouts, retries, concurrency, cancellation, query/response limits, and scoped caching have defined behavior.
- [x] Truncation, missing data, and unsupported metrics remain visible to consumers.

## Verification

Use meaningful HTTP/API fixtures for exact label escaping, cross-project isolation, rate-before-aggregation, counter resets, zero traffic, histogram aggregation, missing buckets, unavailable endpoints, and result limits. Run a bounded live read-only smoke check when the discovery source is reachable.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `backend/app/metrics/{promql,client,catalog,source}.py`
  - `backend/app/domain/interfaces.py` (`DiscoveredValues`, `EntityMapping`, `CollectionResult.mappings`)
  - `backend/tests/test_metrics.py`
  - `docs/metrics-catalog.md` (new) and `docs/contracts.md`
- Commands/checks and results:
  - `uv run pytest` passes (174 passed, 2 skipped); ruff and mypy strict are clean.
  - The metrics tests use `httpx.MockTransport` fixtures and cover:
    - label escaping and scope checks for every catalog query and gate, under plain and hostile scopes
    - rejection of an unscoped ratio operand, bare metrics, fake scopes inside strings, and another project's matcher
    - structural rate-before-aggregation and histogram `le` checks
    - chunk contiguity and merging, NaN → gap, retry/no-retry classification, auth/unreachable/too-large errors
    - cache keying, bearer token only in headers, grid placement
    - capability gating (missing metrics, zero limits, missing buckets, partial containers, mean fallback)
    - route top-N with `(other routes)`, per-query truncation, exclusions on failure, out-of-scope result filtering, cancellation, the service→host mapping join, and discovery truncation and escaping
  - Live read-only smoke on paas/production passed: all 31 catalog queries evaluated as instant queries; full capabilities took 1.2 s and collection 42.6 s (126 requests, 360 series, no exclusions). See `docs/metrics-catalog.md`.
- Decisions or dependency changes:
  - HTTP/RPC failure ratios are derived in the analysis from separately collected operands.
  - Restart evidence comes from Swarm task/replica proxies, not cAdvisor.
  - Container identity strips the Swarm task ID.
  - There is no swap signal where SwapTotal is 0.
  - PSI memory/IO pressure and host OOM kills were added.
  - No new dependencies.
- Remaining limitations or blockers:
  - Mappings are an instant snapshot at T rather than per-step joins; they are used only for latest-day grouping, and T005 labels any time-only overlap as a hypothesis.
  - Traefik is excluded.
  - Collection for large scopes (paas-gpu) was not re-measured end to end; this is a T010 obligation.
- Next ready task: T005.
