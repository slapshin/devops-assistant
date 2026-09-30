# T003 — Read-only telemetry discovery

Dependencies: T001. Status: see [task index](README.md).

## Outcome

Implementing agents know which metrics, labels, history, and resource mappings the configured backend can actually provide.

## Ownership

`docs/telemetry-inventory.md` and sanitized telemetry fixtures.

## Work

- Check connectivity to the configured API; identify compatible endpoints, authentication needs, and observed history without changing the metrics backend.
- Discover project/env pairs, relevant metric families, label sets, approximate series counts, scrape cadence, and data coverage using bounded queries.
- Inventory node, container, HTTP, and RPC signals. Verify histogram types/units, container limits, restart signals, and possible service-to-host mappings.
- Record what is absent and how detection should degrade. Observed oldest data is evidence of coverage, not proof of configured retention.
- If the backend cannot be reached, document the blocker and provide an explicit unverified capability manifest based on supplied examples; dependent agents can use fixtures.

## Acceptance

- [ ] Connectivity, backend API compatibility, authentication needs, and observed coverage are recorded as verified or explicitly unverified.
- [ ] Node/container/HTTP/RPC capabilities map to observed metrics or explicit unsupported/unverified states.
- [ ] Histogram type/units, scrape cadence, identity labels, restart signals, and possible host mappings are documented.
- [ ] Queries are bounded and do not modify the metrics backend or export unrestricted series.
- [ ] If access is unavailable, the limitation and fixture-based fallback are recorded; no guessed capability is labeled verified.

## Verification

Use bounded metadata and time-range queries against `METRICS_URL`. Record query scope, observed counts/history, and sanitized examples. An unavailable source can yield a completed discovery inventory only when its unverified status and downstream validation obligation are explicit; live validation remains a T010 requirement when access is available.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
