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

- [x] Connectivity, backend API compatibility, authentication needs, and observed coverage are recorded as verified or explicitly unverified.
- [x] Node/container/HTTP/RPC capabilities map to observed metrics or explicit unsupported/unverified states.
- [x] Histogram type/units, scrape cadence, identity labels, restart signals, and possible host mappings are documented.
- [x] Queries are bounded and do not modify the metrics backend or export unrestricted series.
- [x] If access is unavailable, the limitation and fixture-based fallback are recorded; no guessed capability is labeled verified.

## Verification

Use bounded metadata and time-range queries against `METRICS_URL`. Record query scope, observed counts/history, and sanitized examples. An unavailable source can yield a completed discovery inventory only when its unverified status and downstream validation obligation are explicit; live validation remains a T010 requirement when access is available.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `docs/telemetry-inventory.md` (new)
  - `fixtures/metrics/capabilities_paas_production_observed.json` (new, generated), with its generator code in `backend/scripts/generate_fixtures.py` and a test in `backend/tests/test_contracts.py`
  - `docs/DECISIONS.md` §5/§6/§9 (scrape interval, retention edge, exclusions, RPC failure, container limits, service→host mapping, range chunking)
  - `docs/contracts.md` (fixture table)
- Commands/checks and results:
  - About 40 bounded read-only GET requests through the SSH tunnel to `localhost:8428`. The endpoints and results are listed in the inventory. All requests finished in ≤ 1 s except the paas-gpu cost probe (22.6 s).
  - Nothing was written to the source.
  - `make check` passes (58 backend tests passed, 2 skipped; 7 frontend tests).
- Decisions or dependency changes:
  - The rate window stays at 300 s.
  - Range queries are split into ≤ 7-day chunks.
  - Container memory-vs-limit and CPU throttling are unsupported for paas/production.
  - RPC failure is defined as a non-`OK` status.
  - The service-to-host mapping is verified by labels and joined per step.
  - `error_type` must not classify failures.
  - The detector config does not yet have thresholds for the new RPC/container signals (`rpc_request_rate`, `rpc_error_ratio`, `rpc_latency_quantile`, `container_memory_working_set`, OOM/restarts); T005 adds them.
- Remaining limitations or blockers:
  - Only paas/production was deep-inspected. Other scopes are unverified.
  - Authentication was assessed only through the local tunnel.
  - The retention flag (4w) and the observed oldest data (≈ 28.4 d) are point-in-time.
  - Traefik metrics are present but excluded from the first-release catalog pending review of their `code` label.
  - T010 must repeat live validation, including a large scope.
- Correction (2026-09-30, T005): the "paas-production-2 / 91 % used" note was a misread free ratio (91 % free); the inventory now says so.
- Next ready task: T004 (dependencies T002 and T003 are done). T005 is also ready.
