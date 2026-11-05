# T026 — Notification channels

Dependencies: T025. Status: see [task index](README.md).

## Outcome

**Notification channels** are configured once for the whole application, like Grafana contact points but simpler: a named destination of one kind, e.g. "On-call email" (a list of addresses) or "Ops chat" (a Telegram chat, T027). A channel says only *where*. A project's report schedule picks the channels its scheduled reports go to, each with its own rule saying *when* (after every run, or on problems at or above a severity). One channel serves many projects with different rules, and editing it applies to all of them. Each finished scheduled analysis records one delivery outcome per channel; channels are delivered independently, so a broken Telegram chat never stops the email. Manual analyses are never sent.

## Why

T025 hard-wired one channel into the schedule (`ReportSchedule.notify: EmailNotification`), the job (`notification`) and the API (`/test-email`). A per-project list of destinations would make every project repeat the same addresses and chat IDs, and a bot token rotation would touch every project. Separating *where* (channel, app-level) from *when* (rule, per project) keeps the project form short and makes a new messenger a new channel kind, not a schedule change.

## Decisions

### Model (`app/domain/notifications.py`)

- `NotificationChannel`: `channel_id`, `name` (unique, case-insensitive), `description`, `destination` discriminated by `kind` (`ChannelKind`: `email`, `telegram`), `readable`, `used_by` (projects whose schedule sends to it), timestamps. Input `NotificationChannelInput`: name, description, `destination` (`EmailDestination` | `TelegramDestinationInput`).
- **A channel holds its own secret** (the Telegram bot token): Fernet-encrypted in `notification_channels.secrets`, write-only like credentials (input `bot_token`, output `bot_token_set`; omitted on update keeps the stored one while the kind is unchanged). Credentials stay for data sources only.
- `ScheduleNotification` on `ReportSchedule.notifications` (at most 10, each channel once): `channel_id`, `enabled`, `policy` (`when: always | problems`, `min_severity`, defaults *problems* / *high*). The T025 policy logic (`skip_reason`) is unchanged.
- `DeliveryOutcome` list on `AnalysisJob.notifications`: `channel_id` (null for T025 records), `name` (the channel's name at delivery time), `kind`, `status: sent | skipped | failed | not_configured`, `destinations`, `reason`, `at`.

### Storage

- Table `notification_channels` (public destination JSON + encrypted secrets) and `schedule_notifications` (`project_id` cascade, `channel_id` **restrict**, `position`, `enabled`, `policy`). The schedule JSON holds only timing (`ReportSchedule.timing()`), so editing notifications never re-anchors a schedule; removing the schedule removes its notifications. A channel in use cannot be deleted (`409 channel_in_use`).
- Migration `0009_notification_channels` (upgrade-only): every T025 `notify` becomes an email channel (projects with the same recipients share one, named "Email <first recipient> [+N]") plus a schedule notification with the T025 policy; `notify` leaves the schedule JSON and `schedule_anchor` is untouched. Stored jobs' `notification` becomes `notifications` (one email outcome or empty).

### Delivery

```text
JobRunner._notify(job)                        (scheduled jobs only, ≤60 s)
  └─ ReportNotifier.job_finished(job) -> list[DeliveryOutcome]
       ├─ project's current schedule → enabled notifications
       ├─ ReportMessage built once (channel-neutral content rules)
       └─ per notification, concurrently (≤20 s each):
            channel + secrets from the repository (current destination)
            policy → skipped | driver unavailable → not_configured
            driver.deliver(destination, secrets, message, silent) → sent | failed(reason)
```

- `app/notifications/message.py`: `ReportMessage` holds the content rules once for every kind (never "healthy" with unevaluated signals, partial/failed called out, AI summary unverified, top 10 findings).
- **Drivers** (`app/notifications/drivers/`): one per kind behind `ChannelDriver` (`base.py`: `unavailable()`, `deliver()`, `test()`); errors are `DeliveryError` with a reason that never holds a secret; any other exception is recorded by type name only. Registry `build_drivers(settings)`; tests inject drivers with `create_app(..., drivers=…)`. Email: `drivers/email/` (T025 renderer and transport, unchanged output).

### API (`app/api/notification_channels.py`)

`/api/notification-channels`: `GET`, `POST`, `GET/PUT/DELETE /{channel_id}`; `GET /kinds` (each kind with `available` and the server's reason); `POST /test` (`{channel_id?, destination}`: tests an unsaved form; an omitted token comes from `channel_id`). `POST /api/notifications/test-email` and `RuntimeConfig.email_configured` are removed.

### Frontend

- `/notification-channels` page (rail icon): table and form, mirroring *Credentials*; destination editors per kind (`components/notifications/EmailDestination.vue`, `TelegramDestination.vue`) chosen from `lib/notifications.ts` (`KINDS` registry: empty draft, draft from stored, input, validation, description).
- Project form → Schedule → **Notifications** (`ScheduleNotifications.vue`): rows of channel + rule, *Add notification*, link to the channels page.
- Project page lists "**Ops chat** (Telegram · chat …) on failures, …"; analysis history shows "· 1 of 2 notifications sent" with per-channel reasons on hover.

## Acceptance

- [x] Notification channels are managed on their own page; names are unique; secrets are write-only and encrypted; a channel in use cannot be deleted.
- [x] A schedule sends to up to 10 channels, each with its own rule; one channel serves several projects with different rules; editing a channel applies from the next run.
- [x] Only scheduled analyses are delivered; removing the schedule removes its notifications; editing notifications never re-anchors the schedule.
- [x] One outcome per channel; a failing or slow channel never affects another or the job state.
- [x] T025 configurations migrate to shared email channels with identical recipients and policies; old jobs keep their email outcome; schedules neither fire again nor skip.
- [x] Email output is byte-for-byte the T025 output.
- [x] `make check` passes.

## Completion record

Implemented together with T027.

- **Design history:** a first pass put destinations (and a Telegram credential reference) on each project's schedule as "targets"; on review the owner chose app-level channels (Grafana-like), rules per project, and the bot token inside the channel. Nothing of the first pass was committed; migration 0009 and the contracts describe only the final model.
- **Verification:** `make check` passes. Email output compared byte-for-byte with the T025 renderer over 89 fixture combinations (states × time zones × links + failed): identical (one-off script, not committed). Backend `tests/test_notifications.py`: contracts, policy, email content, channel CRUD and write-only token, usage and delete protection, unknown channels, round trip without re-anchoring, fan-out isolation, timeouts, secret-free unexpected errors, disabled notifications, an edited channel used on the next run, one channel with different rules per project, migration from a T025-shaped database (shared recipients → one channel), API. Frontend `lib/notifications.test.ts` and `App.test.ts` (channels page, schedule picker, project page, history). Both pages checked in a running demo (fake providers).
- **Demo:** `DEMO_PROJECTS=true` seeds "Demo: on-call email" and "Demo: Ops chat" and gives "Demo: incident" a daily schedule sending to both.
- **Remaining limitations:** no retries/queue, digests, routing by labels, or "only on change"; a job interrupted by a restart is not delivered; SMTP stays server-wide; creating a channel from the project form opens the channels page in a new tab instead of in place.
