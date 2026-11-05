<script setup lang="ts">
/** The notification channels a project's schedule sends to, each with its own rule (T026). */
import { computed } from "vue";
import { useChannelKinds, useNotificationChannels } from "../../api/queries";
import type { FieldErrors } from "../../lib/auth";
import ChannelIcon from "./ChannelIcon.vue";
import {
  MAX_SCHEDULE_NOTIFICATIONS,
  NOTIFY_SEVERITIES,
  type NotificationDraft,
  describeDestination,
  newNotification,
} from "../../lib/notifications";

const notifications = defineModel<NotificationDraft[]>({ required: true });
defineProps<{ errors: FieldErrors }>();

const channels = useNotificationChannels();
const kinds = useChannelKinds();
const items = computed(() => channels.data.value?.items ?? []);
const byId = computed(() => new Map(items.value.map((c) => [c.channel_id, c])));
const unavailable = computed(() => new Map((kinds.data.value?.items ?? []).filter((k) => !k.available).map((k) => [k.kind, k.reason ?? "Not configured on the server."])));
/** A channel not listed yet, to preselect when adding. */
const nextChannel = computed(() => items.value.find((c) => !notifications.value.some((n) => n.channelId === c.channel_id))?.channel_id ?? "");

const field = (i: number) => `schedule.notifications.${i}.channel_id`;
const reasonOf = (channelId: string) => {
  const channel = byId.value.get(channelId);
  return channel ? unavailable.value.get(channel.destination.kind) : undefined;
};

function add() {
  notifications.value = [...notifications.value, newNotification(nextChannel.value)];
}

function remove(index: number) {
  notifications.value = notifications.value.filter((_, i) => i !== index);
}
</script>

<template>
  <div class="notifications stack">
    <h3 class="title">Notifications</h3>
    <p class="muted hint">
      Send each scheduled report to <RouterLink to="/notification-channels" target="_blank">notification channels</RouterLink>, each with its own
      rule. Manually started analyses are never sent.
    </p>
    <p v-if="errors['schedule.notifications']" class="field-error">{{ errors["schedule.notifications"] }}</p>
    <p v-if="channels.isSuccess.value && !items.length" class="banner">
      No notification channels yet. <RouterLink to="/notification-channels" target="_blank">Create one</RouterLink>, then add it here.
    </p>

    <section v-for="(n, i) in notifications" :key="n.key" class="notification" :aria-label="`Notification ${i + 1}`">
      <div class="field channel">
        <label :for="`${n.key}-channel`">Channel</label>
        <select
          :id="`${n.key}-channel`"
          v-model="n.channelId"
          :aria-invalid="!!errors[field(i)]"
          :aria-describedby="errors[field(i)] ? `err-${n.key}-channel` : undefined"
        >
          <option value="" disabled>Choose a channel…</option>
          <option v-for="c in items" :key="c.channel_id" :value="c.channel_id">{{ c.name }}</option>
        </select>
        <p v-if="byId.get(n.channelId)" class="muted hint destination"><ChannelIcon :kind="byId.get(n.channelId)!.destination.kind" />{{ describeDestination(byId.get(n.channelId)!.destination) }}</p>
        <p v-if="errors[field(i)]" :id="`err-${n.key}-channel`" class="field-error">{{ errors[field(i)] }}</p>
        <p v-if="reasonOf(n.channelId)" class="hint v-critical">{{ reasonOf(n.channelId) }} Nothing is sent until then.</p>
      </div>
      <div class="field" style="grid-area: when">
        <label :for="`${n.key}-when`">Send</label>
        <select :id="`${n.key}-when`" v-model="n.when">
          <option value="problems">On problems</option>
          <option value="always">After every run</option>
        </select>
      </div>
      <div class="field" style="grid-area: severity">
        <label :for="`${n.key}-severity`">{{ n.when === "problems" ? "Findings that send" : "Findings that alert" }}</label>
        <select :id="`${n.key}-severity`" v-model="n.minSeverity" :title="n.when === 'problems' ? 'Failed and partial analyses are always sent.' : 'Other reports arrive silently where the channel supports it (Telegram).'">
          <option v-for="s in NOTIFY_SEVERITIES" :key="s.severity" :value="s.severity">{{ s.label }}</option>
        </select>
      </div>
      <div class="end">
        <label class="check"><input v-model="n.enabled" type="checkbox"> Enabled</label>
        <button type="button" class="danger" :aria-label="`Remove notification ${i + 1}`" @click="remove(i)">Remove</button>
      </div>
    </section>

    <p class="muted hint">
      On problems: failed and partial analyses, and findings at or above the chosen severity. After every run: reports below that severity arrive
      silently where the channel supports it (Telegram).
    </p>
    <div class="row">
      <button type="button" :disabled="notifications.length >= MAX_SCHEDULE_NOTIFICATIONS || !items.length" @click="add">Add notification</button>
      <span v-if="notifications.length >= MAX_SCHEDULE_NOTIFICATIONS" class="muted hint">At most {{ MAX_SCHEDULE_NOTIFICATIONS }} channels.</span>
    </div>
  </div>
</template>

<style scoped>
.destination { display: inline-flex; align-items: center; gap: 6px; }
.title { margin: calc(var(--space) * 2) 0 0; font-size: 0.95rem; }
.hint { font-size: 0.85rem; margin: 0; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.notification {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  grid-template-areas: "channel channel end" "when severity .";
  gap: var(--space);
  align-items: start;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space) calc(var(--space) * 1.5);
}
.channel { grid-area: channel; }
.end { grid-area: end; display: flex; gap: var(--space); align-items: center; margin-top: 22px; }
.check { display: flex; gap: 6px; align-items: center; }
@media (max-width: 720px) {
  .notification { grid-template-columns: minmax(0, 1fr); grid-template-areas: "channel" "when" "severity" "end"; }
  .end { margin-top: 0; }
}
</style>
