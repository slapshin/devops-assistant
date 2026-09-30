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

- [ ] Every selector is correctly scoped and escaped, including ratio operands and selected-scope discovery.
- [ ] Counter rates, aggregation, reset behavior, invalid denominators, histogram units, and missing series are handled correctly.
- [ ] Metric capabilities explicitly gate latency, restart, and limit-dependent calculations.
- [ ] Timeouts, retries, concurrency, cancellation, query/response limits, and scoped caching have defined behavior.
- [ ] Truncation, missing data, and unsupported metrics remain visible to consumers.

## Verification

Use meaningful HTTP/API fixtures for exact label escaping, cross-project isolation, rate-before-aggregation, counter resets, zero traffic, histogram aggregation, missing buckets, unavailable endpoints, and result limits. Run a bounded live read-only smoke check when the discovery source is reachable.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
