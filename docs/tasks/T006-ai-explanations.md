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

- [ ] OpenAI, disabled, and fake providers satisfy the same domain-level interface.
- [ ] Structured output and evidence references are validated; invented references are rejected.
- [ ] Calculated fields stay authoritative, and possible causes remain hypotheses.
- [ ] Evidence/label payloads obey configured bounds and contain no credentials.
- [ ] Missing key, refusal, timeout, rate limit, malformed/incomplete output, and provider errors preserve numerical reporting.

## Verification

Run adapter and contract tests with fake SDK/API responses, including label text that resembles instructions and invented evidence IDs. Check payload size and reference validation. Normal automated tests must not require paid live model calls.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
