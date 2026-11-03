# T025 — Email notifications for scheduled reports

Dependencies: T024. Status: see [task index](README.md).

## Outcome

A project's report schedule can email each scheduled report to up to 20 recipients, either after every run or only on problems: a failed or partial analysis, or a finding at or above a minimum severity (*high* by default). Manual analyses are never emailed. Delivery is a side effect: its outcome is recorded on the job and never changes the job's state.

## Decisions

- **SMTP is configured on the server** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_TLS_VERIFY`, `SMTP_USERNAME`/`SMTP_PASSWORD`, `SMTP_FROM`), like the AI provider; **recipients are configured per project** on the schedule (`ReportSchedule.notify`, stored in the existing schedule JSON, so no migration). `EMAIL_PROVIDER=fake` writes `.eml` files to `DATA_DIR/outbox` (tests, `make demo`); `none` turns email off.
- **Stdlib `smtplib`** in a thread with a 20 s socket timeout; no new dependency. **No retries**: a failure is recorded with its reason and the next scheduled run tries again.
- **Hook in the runner, not the scheduler**: only the runner knows the final state, and the email goes out after the AI explanation. `JobRunner` calls a `JobNotifier` for `trigger: scheduled` jobs that finished `completed`, `partial` or `failed` (not cancelled, not interrupted by a restart), bounded by 60 s; the worker waits for it before the next job.
- **Outcome on the job** (`AnalysisJob.notification`: `sent | skipped | failed | not_configured`, recipient count, reason, time). Jobs are stored as JSON, so no migration. The notifier reads the project's current schedule when the job finishes, so recipients removed meanwhile receive nothing.
- **Content** from `ReportSummary` only: subject `[DevOps Assistant] <project>: 2 critical, 1 high (partial)` / `no anomalies found` / `analysis failed`; body with window (in the schedule's time zone), state, counts, the 10 most severe findings, the AI summary when it succeeded (marked unverified), unevaluated families ("No anomaly there does not mean healthy"), exclusions, and a link when `APP_PUBLIC_URL` is set. Report values are HTML-escaped; header values cannot contain line breaks; recipients are plain addresses only (no display names, lists or quoting).

## Changes

- Backend: `app/domain/notifications.py` (new), `app/domain/schedule.py` (`notify`), `app/domain/jobs.py` (`notification`, `email_configured`, `email_not_configured`/`email_failed` codes), `app/domain/interfaces.py` (`JobNotifier`, `ReportRepository.set_notification`), `app/notifications/` (new: `notifier.py`, `render.py`, `transport.py`), `app/api/notifications.py` (new: `POST /api/notifications/test-email`), `app/jobs.py` (notify after finishing; `idle()`), `app/settings.py`, `app/storage/repository.py`, `app/main.py`, `app/container.py`, `app/api/routes.py`. Tests: `tests/test_notifications.py` (new), `tests/test_schedules.py`, `tests/conftest.py`.
- Frontend: `api/client.ts`, `api/queries.ts` (`useTestEmail`), `lib/projects.ts` (draft fields, `parseRecipients`, `recipientsError`, `notifySummary`, `NOTIFICATION_LABELS`), `views/projects/ProjectFormView.vue` (Email section, test email), `views/projects/ProjectView.vue`, `components/AnalysesTable.vue`. Tests: `App.test.ts`.
- Generated: `docs/contracts/*`, `frontend/src/api/schema.d.ts`, `fixtures/**` (new `jobs/job_scheduled_emailed.json`; the fixture schedule emails).
- Config and docs: `config.env.template`, `tools/compose/compose.yml`, `Makefile` (`make demo` uses the outbox), `DECISIONS.md` §2/§4, `OPERATIONS.md`, `ARCHITECTURE.md`, `UI_SPEC.md` §3, `contracts.md`, `AGENTS.md`.

## Acceptance

- [x] A schedule can carry recipients, a policy (`always` / `problems`) and a minimum severity; invalid addresses are rejected client- and server-side.
- [x] A finished scheduled analysis is emailed when the policy matches; manual analyses and projects without recipients send nothing.
- [x] Delivery failures, timeouts and missing configuration are recorded on the job and never change its state or report.
- [x] The email never presents unevaluated or partial data as healthy, escapes report values, and links to the report when `APP_PUBLIC_URL` is set.
- [x] *Send test email* checks the configuration; the UI says when the server has no email configured.
- [x] `make check` passes.

## Verification

- `tests/test_notifications.py`: contract validation (header injection, display names, lists), policy, rendering (findings, healthy, partial, failed, escaping), end-to-end delivery through the fake outbox with a scheduler tick, not-configured and failing transports, SMTP call sequence (STARTTLS + login) against a fake `smtplib.SMTP`, test-email API, settings validation.
- Not verified against a real SMTP relay; the SMTP path is covered with a fake `smtplib.SMTP` only.

## Remaining limitations / follow-ups

- No retries or digest; one email per scheduled run.
- A job interrupted by a restart is marked failed at startup without an email.
- The worker waits for delivery (≤ 60 s) before the next analysis.
- SMTP settings are server-wide; a UI-managed SMTP credential would need a new credential kind.
