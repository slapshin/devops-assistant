import type {
  ChannelKind,
  DeliveryOutcome,
  Destination,
  DestinationInput,
  NotificationChannel,
  NotificationChannelInput,
  NotificationStatus,
  NotifyWhen,
  ScheduleNotification,
  Severity,
} from "../api/client";
import type { FieldErrors } from "./auth";

/** Mirrors backend/app/domain/notifications.py (T026, T027). */
export const MAX_SCHEDULE_NOTIFICATIONS = 10;
const MAX_CHANNEL_NAME_CHARS = 100;
export const MAX_RECIPIENTS = 20;
const MAX_EMAIL_CHARS = 254;
const EMAIL = /^[^@\s,;:<>()[\]"\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$/;
const TELEGRAM_CHAT_ID = /^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$/;
const TELEGRAM_TOKEN = /^\d{5,}:[A-Za-z0-9_-]{30,}$/;
export const NOTIFY_SEVERITIES: { severity: Severity; label: string }[] = [
  { severity: "critical", label: "Critical" },
  { severity: "high", label: "High or worse" },
  { severity: "medium", label: "Medium or worse" },
  { severity: "low", label: "Any finding" },
];
const DEFAULT_NOTIFY_SEVERITY: Severity = "high";

export const DELIVERY_LABELS: Record<NotificationStatus, string> = {
  sent: "sent",
  skipped: "not sent",
  failed: "failed",
  not_configured: "not configured",
};

// --- channel destinations ------------------------------------------------------------------------

export interface EmailDraft {
  kind: "email";
  /** Comma-, semicolon- or whitespace-separated addresses. */
  recipients: string;
}

export interface TelegramDraft {
  kind: "telegram";
  /** A new token; empty keeps the stored one. */
  botToken: string;
  /** Whether the stored channel holds a token (null for a new channel). */
  botTokenSet: boolean | null;
  chatId: string;
  /** Forum topic; empty for the main chat. */
  threadId: string;
}

export type DestinationDraft = EmailDraft | TelegramDraft;
type Of<K extends ChannelKind, T extends { kind: ChannelKind }> = Extract<T, { kind: K }>;

interface KindSpec<K extends ChannelKind> {
  label: string;
  empty: () => Of<K, DestinationDraft>;
  draftFrom: (destination: Of<K, Destination>) => Of<K, DestinationDraft>;
  input: (draft: Of<K, DestinationDraft>) => Of<K, DestinationInput>;
  /** Errors keyed relative to the destination ("recipients", "chat_id", …). */
  validate: (draft: Of<K, DestinationDraft>) => FieldErrors;
  /** "oncall@example.com", "chat -100…, topic 12" */
  describe: (destination: Of<K, Destination>) => string;
}

/** Unique (case-insensitively) addresses of a comma-, semicolon- or whitespace-separated list, in order. */
export function parseRecipients(raw: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const address of raw.split(/[\s,;]+/).filter(Boolean)) {
    const key = address.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(address);
  }
  return result;
}

/** Field error for a recipient list, or null when it is valid. */
export function recipientsError(raw: string): string | null {
  const recipients = parseRecipients(raw);
  if (!recipients.length) return "Add at least one email address";
  const bad = recipients.filter((a) => a.length > MAX_EMAIL_CHARS || !EMAIL.test(a));
  if (bad.length) return `Invalid email addresses: ${bad.join(", ")}`;
  if (recipients.length > MAX_RECIPIENTS) return `At most ${MAX_RECIPIENTS} addresses`;
  return null;
}

const EMAIL_SPEC: KindSpec<"email"> = {
  label: "Email",
  empty: () => ({ kind: "email", recipients: "" }),
  draftFrom: (d) => ({ kind: "email", recipients: d.recipients.join(", ") }),
  input: (d) => ({ kind: "email", recipients: parseRecipients(d.recipients) }),
  validate: (d): FieldErrors => {
    const error = recipientsError(d.recipients);
    return error ? { recipients: error } : {};
  },
  describe: (d) => (d.recipients.length === 1 ? d.recipients[0]! : `${d.recipients[0]} +${d.recipients.length - 1}`),
};

const TELEGRAM_SPEC: KindSpec<"telegram"> = {
  label: "Telegram",
  empty: () => ({ kind: "telegram", botToken: "", botTokenSet: null, chatId: "", threadId: "" }),
  draftFrom: (d) => ({
    kind: "telegram",
    botToken: "",
    botTokenSet: d.bot_token_set,
    chatId: d.chat_id,
    threadId: d.message_thread_id != null ? String(d.message_thread_id) : "",
  }),
  input: (d) => ({
    kind: "telegram",
    ...(d.botToken.trim() ? { bot_token: d.botToken.trim() } : {}),
    chat_id: d.chatId.trim(),
    message_thread_id: d.threadId.trim() ? Number(d.threadId.trim()) : null,
  }),
  validate: (d) => {
    const errors: FieldErrors = {};
    const token = d.botToken.trim();
    if (!token && !d.botTokenSet) errors.bot_token = "Required";
    else if (token && !TELEGRAM_TOKEN.test(token)) errors.bot_token = "Not a bot token (expected 123456:ABC-… from @BotFather)";
    const chat = d.chatId.trim();
    if (!chat) errors.chat_id = "Required";
    else if (!TELEGRAM_CHAT_ID.test(chat)) errors.chat_id = "A numeric chat ID such as -1001234567890, or @channel_username";
    const thread = d.threadId.trim();
    if (thread && !/^[1-9]\d{0,15}$/.test(thread)) errors.message_thread_id = "A positive whole number";
    return errors;
  },
  describe: (d) => `chat ${d.chat_id}${d.message_thread_id != null ? `, topic ${d.message_thread_id}` : ""}`,
};

/** Every channel kind the UI knows; adding a messenger adds an entry here and a destination component. */
export const KINDS: { [K in ChannelKind]: KindSpec<K> } = {
  email: EMAIL_SPEC,
  telegram: TELEGRAM_SPEC,
};

export const CHANNEL_KINDS = Object.keys(KINDS) as ChannelKind[];

// The registry is keyed by kind, so a draft or destination always reaches its own spec.
const specOf = (kind: ChannelKind) => KINDS[kind] as unknown as KindSpec<ChannelKind>;

export const kindLabel = (kind: ChannelKind) => KINDS[kind].label;
export const emptyDestination = (kind: ChannelKind): DestinationDraft => specOf(kind).empty();
export const destinationDraftFrom = (destination: Destination): DestinationDraft => specOf(destination.kind).draftFrom(destination);
export const destinationInput = (draft: DestinationDraft): DestinationInput => specOf(draft.kind).input(draft);
export const validateDestination = (draft: DestinationDraft): FieldErrors => specOf(draft.kind).validate(draft);
/** "Telegram · chat -100…", "Email · oncall@example.com +1". */
export const describeDestination = (destination: Destination) => `${kindLabel(destination.kind)} · ${specOf(destination.kind).describe(destination)}`;

// --- channels ------------------------------------------------------------------------------------

export interface ChannelDraft {
  name: string;
  description: string;
  destination: DestinationDraft;
}

export const emptyChannel = (kind: ChannelKind = "telegram"): ChannelDraft => ({ name: "", description: "", destination: emptyDestination(kind) });

export const channelDraftFrom = (channel: NotificationChannel): ChannelDraft => ({
  name: channel.name,
  description: channel.description ?? "",
  destination: destinationDraftFrom(channel.destination),
});

export const channelInput = (draft: ChannelDraft): NotificationChannelInput => ({
  name: draft.name.trim(),
  description: draft.description.trim() || null,
  destination: destinationInput(draft.destination),
});

/** Errors keyed like the server's: "name", "destination.recipients", "destination.chat_id". */
export function validateChannel(draft: ChannelDraft): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = "Required";
  else if (name.length > MAX_CHANNEL_NAME_CHARS) errors.name = `At most ${MAX_CHANNEL_NAME_CHARS} characters`;
  for (const [field, message] of Object.entries(validateDestination(draft.destination))) errors[`destination.${field}`] = message;
  return errors;
}

/** Server validation errors about a destination carry the union tag ("destination.telegram.chat_id"). */
export const normalizeDestinationField = (field: string) => field.replace(/(^|\.)destination\.(email|telegram)\./, "$1destination.");

// --- schedule notifications ----------------------------------------------------------------------

export interface NotificationDraft {
  /** Stable key for list rendering. */
  key: string;
  channelId: string;
  enabled: boolean;
  when: NotifyWhen;
  minSeverity: Severity;
}

let nextKey = 0;
const newKey = () => `notification-${++nextKey}`;

export const newNotification = (channelId = ""): NotificationDraft => ({
  key: newKey(),
  channelId,
  enabled: true,
  when: "problems",
  minSeverity: DEFAULT_NOTIFY_SEVERITY,
});

export const notificationDraftFrom = (n: ScheduleNotification): NotificationDraft => ({
  key: newKey(),
  channelId: n.channel_id,
  enabled: n.enabled ?? true,
  when: n.policy?.when ?? "problems",
  minSeverity: n.policy?.min_severity ?? DEFAULT_NOTIFY_SEVERITY,
});

export const notificationInput = (draft: NotificationDraft): ScheduleNotification => ({
  channel_id: draft.channelId,
  enabled: draft.enabled,
  policy: { when: draft.when, min_severity: draft.minSeverity },
});

/** Errors keyed like the server's: "schedule.notifications.<i>.channel_id". */
export function validateNotifications(notifications: NotificationDraft[]): FieldErrors {
  const errors: FieldErrors = {};
  if (notifications.length > MAX_SCHEDULE_NOTIFICATIONS) errors["schedule.notifications"] = `At most ${MAX_SCHEDULE_NOTIFICATIONS} channels`;
  const seen = new Set<string>();
  notifications.forEach((n, i) => {
    const field = `schedule.notifications.${i}.channel_id`;
    if (!n.channelId) errors[field] = "Choose a channel";
    else if (seen.has(n.channelId)) errors[field] = "This channel is already listed";
    seen.add(n.channelId);
  });
  return errors;
}

/** "on failures, partial runs and high or worse findings" / "after every run". */
export function policySummary(policy: ScheduleNotification["policy"]): string {
  if (policy?.when === "always") return "after every run";
  const severity = policy?.min_severity ?? DEFAULT_NOTIFY_SEVERITY;
  const label = NOTIFY_SEVERITIES.find((s) => s.severity === severity)?.label.toLowerCase() ?? severity;
  const findings = severity === "low" ? "any finding" : severity === "critical" ? "critical findings" : `${label} findings`;
  return `on failures, partial runs and ${findings}`;
}

// --- delivery outcomes ---------------------------------------------------------------------------

/** "Ops chat sent" / "2 of 3 notifications sent", plus whether any needs attention. */
export function deliverySummary(outcomes: DeliveryOutcome[]): { text: string; problem: boolean } | null {
  if (!outcomes.length) return null;
  const sent = outcomes.filter((o) => o.status === "sent").length;
  const problem = outcomes.some((o) => o.status === "failed" || o.status === "not_configured");
  if (outcomes.length === 1) return { text: `${outcomes[0]!.name} ${DELIVERY_LABELS[outcomes[0]!.status]}`, problem };
  return { text: `${sent} of ${outcomes.length} notifications sent`, problem };
}

/** One line per outcome, for a tooltip: "Ops chat (Telegram): failed — Chat not found …". */
export const deliveryDetails = (outcomes: DeliveryOutcome[]) =>
  outcomes.map((o) => `${o.name} (${kindLabel(o.kind)}): ${DELIVERY_LABELS[o.status]}${o.reason ? ` — ${o.reason}` : ""}`).join("\n");
