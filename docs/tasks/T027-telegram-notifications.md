# T027 — Telegram notifications

Dependencies: T026. Status: see [task index](README.md).

## Outcome

A notification channel (T026) can be a **Telegram chat**: a private chat, group, supergroup topic or channel that a bot posts each scheduled report into. The channel holds the bot token (encrypted, write-only) and the chat; projects pick the channel in their schedule. Manual analyses are never posted. Users find the chat ID from the UI instead of digging through the Bot API.

## Decisions

### Destination and secret

```text
TelegramDestinationInput { kind: "telegram", bot_token?: secret, chat_id, message_thread_id? }
TelegramDestination      { kind: "telegram", bot_token_set: bool, chat_id, message_thread_id? }
```

- The **bot token belongs to the channel** (like a Grafana contact point), stored Fernet-encrypted in `notification_channels.secrets`; omitted on update keeps the stored token. Checked loosely (`^\d{5,}:[A-Za-z0-9_-]{30,}$`, whitespace stripped) so a pasted chat ID or URL is rejected early. One bot in several chats means entering its token in each channel; credentials stay for data sources.
- `chat_id` validated as `^-?\d{1,20}$` or `^@[A-Za-z][A-Za-z0-9_]{4,31}$`, stored as a string (opaque identifier; also covers `@username`). Not a secret: a chat ID without the token cannot be posted to.

### Telegram driver (`app/notifications/drivers/telegram/`)

- `client.py` — minimal async **Bot API client on the existing `httpx`** dependency (no `python-telegram-bot`/`aiogram`: we need 4 methods, no polling loop, and pinned deps stay small). Methods: `getMe`, `getChat`, `getUpdates` (chat discovery only), `sendMessage`. Base URL from `TELEGRAM_API_URL` (default `https://api.telegram.org`; supports a self-hosted Bot API server). Outbound proxy via the standard `HTTPS_PROXY` env (`httpx` `trust_env`), documented in `OPERATIONS.md` for networks where Telegram is blocked. 10 s connect / 15 s total timeout.
- **Token redaction is mandatory:** the token is part of the URL path (`/bot<token>/sendMessage`), and `httpx` exceptions and debug logs include the URL. The client never lets an `httpx` exception escape: it maps transport errors to `DeliveryError("Telegram API unreachable: ConnectTimeout")` and API errors to the `description` from Telegram's JSON, passed through `redact(text, token)` anyway. `httpx`'s own logger stays at WARNING. A test asserts the token never appears in outcomes, problem responses or captured logs.
- `render.py` — `ReportMessage` → Telegram **HTML parse mode** (only `<b> <i> <code> <a> <blockquote expandable>`; escape `& < >`, and `"` in `href`):

  ```text
  🔴 <b>shop</b>: 2 critical, 1 high (partial)
  24 h to 2026-11-05 08:00 CET

  <b>critical</b> 5xx ratio on api-gateway
    gateway-1 · 03:10–03:40 · confidence high
  …
  and 4 more

  ⚠️ Not evaluated (insufficient data or source errors): database. No anomaly there does not mean healthy.
  <blockquote expandable>AI summary (unverified): …</blockquote>
  Not included: …
  <a href="https://…/reports/…">Open the report</a>
  ```

  Status marker: 🔴 critical / 🟠 high / 🟡 medium / ⚪ low-only / ✅ no anomalies (only when nothing is unevaluated) / ❗ failed / ◐ partial suffix.
- **Length:** Telegram limits text to 4096 UTF-16 units after entity parsing. Render, measure the HTML string in UTF-16 units (conservative upper bound), and if over, drop findings from the end (adding "and N more"), then truncate the AI summary, then exclusions — never the state line, the unevaluated note or the link. One message per channel, never split.
- **Send options:** `link_preview_options.is_disabled = true`; `message_thread_id` when set; **`disable_notification = true` when the report has no problems** (the schedule's rule decides: with *after every run*, reports below its severity are posted silently, problems ping). No inline URL button: Telegram rejects non-public URLs (e.g. `http://localhost`) in buttons, so the link is in the text and omitted when `APP_PUBLIC_URL` is unset.
- **Errors → reasons** (no auto-retry, consistent with T025, except one case):

  | Bot API response | Outcome reason |
  | --- | --- |
  | 401 | "Bot token rejected — check the channel's token" |
  | 400 `chat not found` | "Chat not found — has the bot been added to it?" |
  | 403 `bot was blocked` / `kicked` / `not a member` | "Bot cannot post in this chat: <description>" |
  | 400 with `parameters.migrate_to_chat_id` | "Group was upgraded to a supergroup; new chat ID is -100…" (config is **not** rewritten silently) |
  | 400 `message thread not found` | "Topic not found (message_thread_id)" |
  | 429 with `retry_after ≤ 5 s` | wait once and retry within the 20 s per-channel budget; otherwise failed with the wait time |
  | network/timeout/5xx | "Telegram API unreachable: <ExceptionType>" |

- `unavailable()` is `None` unless `TELEGRAM_PROVIDER=none`; a missing or unreadable token is a `failed` outcome of that channel, not kind unavailability.
- **Fake provider:** `TELEGRAM_PROVIDER=bot_api|fake|none` (default `bot_api`), mirroring `EMAIL_PROVIDER`. `fake` writes each request as JSON to `DATA_DIR/outbox/telegram/` and answers `getMe`/`getChat`/`getUpdates` with synthetic data; used by tests and `make demo` (the demo seeds a Telegram channel with a fake token).

### API

- `POST /api/notification-channels/test` (T026) with a Telegram destination: calls `getChat` (resolves the title), then sends a short test message; returns `"sent to Ops chat (supergroup)"`. Works for an unsaved form: the token comes from the form or, when omitted, from `channel_id`.
- `POST /api/notification-channels/telegram/chats` with `{bot_token}` or `{channel_id}` → the bot's username and `[{chat_id, title, type, username, is_forum}]` from `getUpdates` (chats that recently messaged or added the bot; Telegram keeps updates ≤ 24 h). Read-only: no `offset` is acknowledged, so it never consumes another consumer's updates. `409 telegram_webhook_set` if the bot has a webhook (then the user pastes the ID). This also checks the token, so there is no separate token test.

### Frontend

- `components/notifications/TelegramDestination.vue` (on the channels page, registered in `lib/notifications.ts` `KINDS`): *Bot token* (password; "Stored — leave empty to keep" when editing), *Chat ID* + **Find chats** (typed token, else the stored one; picking a chat fills the ID), optional *Topic ID*, short how-to. Summary: "Telegram · chat -100…, topic 12".

## Changes

- Backend: `app/domain/notifications.py` (`TelegramDestination(Input)`), `app/notifications/drivers/telegram/` (`client.py`, `render.py`, `driver.py`, `fake.py`), `app/notifications/drivers/__init__.py`, `app/storage/channels.py` (token encryption), `app/api/notification_channels.py` (`/telegram/chats`), `app/settings.py` (`TELEGRAM_PROVIDER`, `TELEGRAM_API_URL`), `app/bootstrap.py`.
- Tests: `tests/test_telegram.py`, plus Telegram parts of `tests/test_notifications.py`.
- Frontend: `components/notifications/TelegramDestination.vue`, `lib/notifications.ts`, `api/queries.ts` (`useTelegramChats`), `App.test.ts`.
- Config/docs: `config.env.template`, `tools/compose/compose.yml`, `Makefile` (`make demo` sets `TELEGRAM_PROVIDER=fake`), `OPERATIONS.md`, `DECISIONS.md`, `UI_SPEC.md`, `AGENTS.md`.

## Acceptance

- [x] A Telegram channel holds a bot token; it is encrypted, write-only and checkable (bot username via *Find chats*); the channel is shared by projects and protected from deletion while a schedule sends to it.
- [x] A Telegram channel receives scheduled reports in a chat, group topic or channel per each project's rule; reports without problems under *after every run* are silent.
- [x] Messages never exceed Telegram's limit, escape all report values, never present unevaluated data as healthy, and link to the report when `APP_PUBLIC_URL` is set.
- [x] Every Bot API failure listed above yields a specific, actionable reason on the outcome; the token never appears in outcomes, API problems or logs.
- [x] *Find chats* lists recent chats of the bot; *Send test* resolves the chat title.
- [x] Email delivery is unaffected when Telegram fails, and vice versa.
- [x] `make check` passes; live verification against a real bot is **not done** (listed below).

## Completion record

- **Built:** token inside the channel (encrypted, write-only, loose format check with whitespace stripped); `drivers/telegram/` with `client.py` (httpx Bot API client: `getMe`, `getChat`, `getUpdates` without offset, `sendMessage` in HTML mode without previews; transport errors reduced to the exception type, API descriptions redacted, `httpx`/`httpcore` request logs silenced), `render.py` (status marker, escaped HTML, expandable AI summary, 4096 UTF-16 budget dropping findings, then summary, then exclusions), `fake.py` (`TELEGRAM_PROVIDER=fake`, JSON outbox), `driver.py` (silent per the schedule's rule, one retry for `retry_after` ≤ 5 s, chat lookup); `POST /api/notification-channels/telegram/chats`; settings `TELEGRAM_PROVIDER`, `TELEGRAM_API_URL` (proxy via `HTTPS_PROXY`); UI editor with *Find chats* and topic ID.
- **Design history:** a first pass kept the token in a `telegram` credential referenced by per-project targets; on review the owner chose app-level channels holding their own token (T026). That pass was never committed.
- **Deviations:** `migrate_to_chat_id` is reported, never applied automatically.
- **Verification:** `backend/tests/test_telegram.py` (rendering, escaping, healthy marker only when everything was evaluated, length budget, request parameters, silent delivery, every error mapping, 429 retry, token never in reasons or captured DEBUG logs, missing token, chat extraction, webhook conflict, API through the fake provider: test with a typed or stored token, chat lookup by token or channel), plus the end-to-end scheduled delivery to email + Telegram in `test_notifications.py`. UI checked in a running demo (fake provider): the channels page, test message and *Find chats* work.
- **Not verified:** no request reached the real Telegram Bot API; behaviour against Telegram is covered by `httpx.MockTransport` responses modelled on the documented error format.

## Out of scope / follow-ups

- Interactive bot (commands, "run analysis" buttons, acknowledgements): needs inbound updates (webhook exposure or a polling loop) and access control — the app has none yet.
- Attachments (report PDF/chart images), message editing on re-runs, per-chat locale.

## Adding the next messenger (checklist, after T026/T027)

1. `ChannelKind` value; destination input/read models in `app/domain/notifications.py` (a secret, e.g. a Slack/Teams/Discord webhook URL, is write-only: `…` in input, `…_set` in output) and its split into public JSON and secrets in `app/storage/channels.py`.
2. `app/notifications/drivers/<kind>/`: `render.py` (`ReportMessage` → native format: Slack Block Kit / Teams Adaptive Card / Discord embed), `client.py` (redacting), `fake.py`, `driver.py`; register in `drivers/__init__.py`.
3. `components/notifications/<Kind>Destination.vue` + a `KINDS` entry in `lib/notifications.ts`; add it to `EDITORS` in `NotificationChannelsView.vue`.
4. Tests: render limits/escaping, error mapping, redaction, fake end-to-end. `make contracts fixtures check`.
