# T006 — OpenAI explanations and provider abstraction

Dependencies: T002, T005. Status: see [task index](README.md).

## Outcome

An optional replaceable AI provider explains numerical findings without controlling the calculations or preventing report delivery.

## Ownership

`backend/app/ai/`, prompts, and provider tests.

## Work

- Implement `ExplanationProvider` with an OpenAI adapter and disabled/fake providers. Keep SDK types inside the adapter.
- Use the Responses API with validated structured output; explicitly handle refusal, incomplete output, timeouts, and rate limits. Follow [OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
- Send bounded evidence summaries and necessary labels, with request/token budgets. Do not send credentials or complete raw time-series payloads.
- Require finding IDs for hypotheses and investigation recommendations. Validate references against the report; structured syntax alone does not establish factual correctness.
- Keep calculated values and severity authoritative in deterministic report fields. Label possible causes as hypotheses and explain missing evidence.
- Treat metric labels as untrusted data. No infrastructure execution or arbitrary LLM-generated queries in this version.
- Preserve the complete numerical report if AI is disabled, unavailable, or invalid. Record explanation status and provider/model metadata separately.

## Acceptance

- [x] OpenAI, disabled, and fake providers satisfy the same domain-level interface.
- [x] Structured output and evidence references are validated; invented references are rejected.
- [x] Calculated fields stay authoritative, and possible causes remain hypotheses.
- [x] Evidence/label payloads obey configured bounds and contain no credentials.
- [x] Missing key, refusal, timeout, rate limit, malformed/incomplete output, and provider errors preserve numerical reporting.

## Verification

Run adapter and contract tests with fake SDK/API responses, including label text that resembles instructions and invented evidence IDs. Check payload size and reference validation. Normal automated tests must not require paid live model calls.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `backend/app/ai/{prompt,validation,openai_adapter,providers}.py`
  - `backend/scripts/check_openai.py` (manual only)
  - `backend/tests/test_ai.py`
  - `app/settings.py` (`AI_PROVIDER=fake`)
  - `app/domain/explanation.py` (`FindingDigest.related_finding_ids`, `host`)
  - `pyproject.toml` (`httpx2` pinned explicitly, since the OpenAI SDK 3.x uses it)
  - `.env.example`, `docs/DECISIONS.md` §2/§7, `docs/contracts.md`
- Commands/checks and results:
  - `uv run pytest` passes (244 passed, 2 skipped); ruff and mypy strict are clean.
  - The AI tests (17) use a mocked Responses API via `httpx2.MockTransport` and cover:
    - success, including a strict json_schema request, `store=false`, and no key or raw series in the payload
    - invented-ID rejection, and failure when only invented IDs remain
    - 429/401/404/500, timeout, connection refused, refusal, incomplete output, schema-invalid output, and an unexpected exception
    - the no-findings skip; disabled, not-configured and fake selection; the fake provider under validation
    - hostile label text (prompt-injection wording kept as data, control characters, URL credentials, length); input budgets
    - SDK confinement to the adapter
  - No paid or live model call was made.
- Decisions or dependency changes:
  - The fake provider is added for demos and tests.
  - Invalid references drop the item; if only invalid items remain, the status is `failed/invalid_output`.
  - `httpx2==2.13.1` is pinned (openai's HTTP stack).
- Remaining limitations or blockers:
  - **Live OpenAI verification was not possible**: no `OPENAI_API_KEY` in this environment, so the model was not selected or verified. The owner runs `scripts.check_openai --structured`, and T010 must record this as unverified unless a key is supplied.
  - Structured syntax does not establish factual correctness; the hypotheses are labelled unverified.
- Next ready task: T007.
