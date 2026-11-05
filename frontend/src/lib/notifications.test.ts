import { describe, expect, it } from "vitest";
import type { DeliveryOutcome, NotificationChannel } from "../api/client";
import {
  channelDraftFrom,
  channelInput,
  deliverySummary,
  describeDestination,
  newNotification,
  normalizeDestinationField,
  notificationDraftFrom,
  notificationInput,
  policySummary,
  scheduleTargets,
  targetsDetails,
  targetsSummary,
  validateChannel,
  validateNotifications,
} from "./notifications";

const TELEGRAM: NotificationChannel = {
  channel_id: "c1",
  name: "Ops chat",
  description: null,
  destination: { kind: "telegram", chat_id: "-100123", message_thread_id: 4, bot_token_set: true },
  readable: true,
  used_by: [],
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const outcome = (name: string, status: DeliveryOutcome["status"]): DeliveryOutcome => ({
  channel_id: null,
  name,
  kind: "email",
  status,
  destinations: 1,
  reason: null,
  at: "2026-11-02T08:00:00Z",
});

describe("notification channels", () => {
  it("keeps a stored bot token unless a new one is typed", () => {
    const draft = channelDraftFrom(TELEGRAM);

    expect(validateChannel(draft)).toEqual({});
    expect(channelInput(draft)).toEqual({
      name: "Ops chat",
      description: null,
      destination: { kind: "telegram", chat_id: "-100123", message_thread_id: 4 },
    });
  });

  it("validates destinations with server-shaped keys", () => {
    const draft = { name: " ", description: "", destination: { kind: "telegram" as const, botToken: "123", botTokenSet: null, chatId: "ops", threadId: "0" } };

    expect(validateChannel(draft)).toEqual({
      name: "Required",
      "destination.bot_token": "Not a bot token (expected 123456:ABC-… from @BotFather)",
      "destination.chat_id": "A numeric chat ID such as -1001234567890, or @channel_username",
      "destination.message_thread_id": "A positive whole number",
    });
    expect(validateChannel({ name: "Mail", description: "", destination: { kind: "email", recipients: "" } })).toEqual({
      "destination.recipients": "Add at least one email address",
    });
  });

  it("drops the kind tag from server error fields and describes destinations", () => {
    expect(normalizeDestinationField("destination.email.recipients.0")).toBe("destination.recipients.0");
    expect(normalizeDestinationField("name")).toBe("name");
    expect(describeDestination(TELEGRAM.destination)).toBe("Telegram · chat -100123, topic 4");
    expect(describeDestination({ kind: "email", recipients: ["a@x.io", "b@x.io"] })).toBe("Email · a@x.io +1");
  });
});

describe("schedule notifications", () => {
  it("round-trips and needs distinct chosen channels", () => {
    const stored = { channel_id: "c1", enabled: false, policy: { when: "always" as const, min_severity: "critical" as const } };

    expect(notificationInput(notificationDraftFrom(stored))).toEqual(stored);
    expect(validateNotifications([newNotification("c1"), newNotification("c1"), newNotification()])).toEqual({
      "schedule.notifications.1.channel_id": "This channel is already listed",
      "schedule.notifications.2.channel_id": "Choose a channel",
    });
    expect(policySummary({ when: "problems", min_severity: "critical" })).toBe("on failures, partial runs and critical findings");
  });

  it("resolves a schedule's channels for display", () => {
    const notifications = [
      { channel_id: "c1", enabled: true, policy: { when: "always" as const, min_severity: "high" as const } },
      { channel_id: "gone", enabled: false },
    ];

    const targets = scheduleTargets(notifications, [TELEGRAM]);

    expect(targets).toEqual([
      { channelId: "c1", name: "Ops chat", destination: "Telegram · chat -100123, topic 4", policy: "after every run", enabled: true },
      { channelId: "gone", name: "Unknown channel", destination: null, policy: "on failures, partial runs and high or worse findings", enabled: false },
    ]);
    expect(targetsSummary(targets)).toBe("Ops chat, Unknown channel (disabled)");
    expect(targetsDetails(targets)).toBe("Ops chat (Telegram · chat -100123, topic 4): after every run\nUnknown channel: disabled");
    expect(targetsSummary(scheduleTargets(undefined, [TELEGRAM]))).toBe("No notifications");
  });

  it("summarizes deliveries", () => {
    expect(deliverySummary([])).toBeNull();
    expect(deliverySummary([outcome("Email", "skipped")])).toEqual({ text: "Email not sent", problem: false });
    expect(deliverySummary([outcome("Email", "sent"), outcome("Chat", "not_configured")])).toEqual({ text: "1 of 2 notifications sent", problem: true });
  });
});
