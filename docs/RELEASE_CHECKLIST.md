# First-release checklist (T010)

Assessed on 2026-09-30 on branch `implementation/first-release`.

## Environment

| Component | Version |
| --- | --- |
| Host | macOS (Darwin 27), Docker Desktop 29.6.1 (buildx 0.35) |
| Image | `devops-ai-assistant:local`: Python 3.14.7, SQLite 3.46.1, uv 0.12.19, UI built on Node 24.21.0; 425 MB, uid 10001 |
| Native tools | uv 0.12.19, Python 3.14.7, Node 26.10 (Node 24.21 verified in Docker) |
| Metrics source | Single-node VictoriaMetrics v1.123.0 reached through an SSH tunnel (`localhost:8428` natively, `host.docker.internal:8428` in Docker); read-only |
| AI | No OpenAI key available. `AI_PROVIDER=fake`, `none`, and a deliberately unreachable OpenAI base URL were used. |

## Repository checks

| Command | Result |
| --- | --- |
| `make check` | Passes. Backend: ruff format/check, mypy strict (54 files), contract and fixture drift checks, pytest **269 passed, 2 skipped**. Frontend: API-type drift check, vue-tsc, ESLint (0 warnings), Vitest **16 passed**, Vite build. |
| `docker build -t devops-ai-assistant:local .` | Succeeds; health check `healthy` |

## Product workflows through the packaged application

All five were run against the Docker image (`docker compose`, port published on 127.0.0.1).

| # | Flow | How | Result |
| --- | --- | --- | --- |
| 1 | Fresh start → select project/env → run → open evidence → reopen saved report | Empty volume; browser (Playwright/Chromium) against the **live** source | 31 projects listed; paas → envs `development, production`. The analysis completed in ≈ 40 s with 10 findings (1 medium other-4xx on `POST /api/v3/tasks`, 9 low disk-throughput bursts), stable trend. Evidence showed the chart and the exact scoped query. Reopened from *Recent reports*. **Pass** |
| 2 | Trends → filter entity/category → select day → episodes and coverage | Browser, live report | `?category=disk_io&entity=paas-production-2&day=3` listed 5 episodes for 27 Sept. The per-day share was recomputed under the filters. The recurring table shows `paas-production-2 · sda` disk_io on 13 of 14 days. **Pass** |
| 3 | Short history, missing buckets, unavailable container/RPC → precise omissions | Image with `synthetic://short-history` and `synthetic://degraded` | Short history: CPU finding at medium confidence (4 baseline days); trend buckets `ok ×2, insufficient_baseline ×3, insufficient_data ×9`; summary `inconclusive`; latency uses mean with the "no histogram buckets" reason. Degraded: containers `unsupported`, RPC signals listed as absent, 4 critical findings, trend `worsening`. **Pass** |
| 4 | Disable or fail AI → complete numerical report and explanation status | Image with `AI_PROVIDER=none`, with `openai` and an unreachable `OPENAI_BASE_URL`, and with `openai` but no key | Status `disabled` / `failed (unavailable)` / `not_configured`; all findings and evidence intact; the job is `completed` in each case. **Pass** |
| 5 | Cancel a run, interrupt another, restart → statuses, saved reports, evidence | Live source; `DELETE` during collection, then `docker compose restart` during a second run | Cancel took < 1 s → `cancelled`. The interrupted job became `failed / interrupted_by_restart` (collection stage `failed`; logged "marked 1 interrupted job(s) failed"). The completed report reopened with 10 findings and 1,530 evidence points. **Pass** after the fix below |

Additional packaged checks from T009: after `down` and `up --force-recreate` the report was byte-identical (MD5); an invalid `METRICS_URL` exits 2 with a precise message; an unreachable source shows health `reachable:false` and a 503 `metrics_source_unavailable`.

## Cross-cutting properties

- **Scope isolation:**
  - Every catalog query and gate is checked for exact escaped `project`/`env` matchers, including under a hostile scope (`tests/test_metrics.py`).
  - Out-of-scope result series are dropped.
  - Report lists are filtered by scope (`tests/test_api.py`).
  - Every live report's evidence queries carry `project="paas", env="production"`.
- **Baseline windows and comparability:** tampering tests show the baselines never use the evaluated day or later data. All 14 buckets use the same detector version and resolution, and every report records `detector_version` and `config_hash`.
- **Honest gaps:**
  - Unsupported signals carry reasons, e.g. swap (SwapTotal = 0), container limits (all 0), throttling (no CFS metrics), and `paas-production-4` (root cgroup only).
  - Missing data is never counted as healthy.
  - HTTP 404 and 5xx are separate findings.
  - Service-to-host relations come only from verified label mappings.
- **AI:** invented finding IDs are dropped; hypotheses are labelled unverified; credentials and control characters are redacted (hardened after the security review); no raw series are sent.
- **Persistence:** real SQLite for tests and containers; migrations are forward-only; reports are immutable and survive restarts and container recreation.
- **UI:**
  - Desktop (1366 px), overlay (1024 px) and narrow (390 px) layouts were checked in a browser, with no horizontal overflow on report views at 390 px.
  - Keyboard: arrow-key tabs, `j`/`k`/`Esc`, and focus return.
  - Every chart has a "Show data" table.
  - Screenshots in `docs/screenshots/` use synthetic data.

## Live verification versus synthetic

| Area | Live (tunnelled VictoriaMetrics) | Synthetic or mocked only |
| --- | --- | --- |
| Discovery, capabilities, scrape cadence, retention | ✔ (T003, T004, T010) | |
| Collection budgets | paas/production ≈ 40 s, 126 requests, 360 series. **paas-gpu/production 200 s, 13 signals, 292 findings, 12.8 MB report, no truncation** | |
| Detection on real data | paas/production and paas-gpu/production | Scenario tests (CPU, memory, disk, 404/5xx, latency, traffic, gaps, zero MAD, short history, changing population) |
| Service → host mapping | ✔ (5 OTel services → paas-production-2) | |
| OpenAI adapter | ✘ **not verified live**: no API key | Mocked Responses API, covering success, refusal, incomplete, timeout, rate limit, auth, invalid output and invented IDs |
| Linux Docker `host-gateway` | ✘ (macOS Docker Desktop only) | |

## Defects found and fixed during validation

1. Cancelled jobs carried `error: internal_error "Cancelled by user."`, contrary to the contract (`error` is null for cancelled jobs). An earlier edit had silently not applied after reformatting. Fixed in `app/jobs.py`, with a regression assertion in `tests/test_api.py`.
2. Job progress could freeze in an unfocused browser tab (timer throttling). Polling now continues in the background (`refetchIntervalInBackground`).
3. Earlier in the release (recorded in the task files): T003 misread a filesystem free ratio as used (corrected); the ECharts markArea timestamps did not match (T008); layout prop fallthrough (T008); credential redaction hardening after a security review (T006).

## Remaining issues and limitations

- **OpenAI is unverified live.** Set `OPENAI_API_KEY`/`OPENAI_MODEL`, then run `cd backend && uv run python -m scripts.check_openai --structured`.
- **Provisional thresholds are noisy on bursty fleets:**
  - paas-gpu/production shows a 10–13 % anomalous share, with 292 latest-day findings, mostly resource spikes;
  - on paas/production, disk-throughput bursts dominate, but they are capped at low severity;
  - tuning via `DETECTOR_CONFIG` is expected after owner review.
- Large scopes produce large reports (12.8 MB for paas-gpu), which are slower to load in the browser. The 20 MB budget applies and trimming is disclosed.
- Mappings are instant snapshots at T. Weekly seasonality is not modelled (14-day baselines).
- There is no authentication (local exposure only), no CI pipeline file, and no published image. Browser checks were run interactively (Playwright MCP), not as a committed e2e suite. Screen readers were not tested with real assistive technology.
- Only paas/production was inventoried in depth. Other scopes rely on runtime capability discovery (paas-gpu/production worked).

## Setup and use

- Run: [README](../README.md) and [OPERATIONS](OPERATIONS.md) (`docker compose up -d --build`, or `make install && make serve`)
- Behaviour: [detection](detection.md), [metrics catalog](metrics-catalog.md), [contracts](contracts.md), [UI spec](UI_SPEC.md)
