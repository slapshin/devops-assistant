<script setup lang="ts">
/** Notification channels (/notification-channels): where scheduled reports can be sent, configured once for every project (T026). */
import { type Component, computed, ref, watch } from "vue";
import { ApiError, type ChannelKind, type NotificationChannel } from "../../api/client";
import { useChannelKinds, useCreateChannel, useDeleteChannel, useNotificationChannels, useTestChannel, useUpdateChannel } from "../../api/queries";
import EmailDestination from "../../components/notifications/EmailDestination.vue";
import TelegramDestination from "../../components/notifications/TelegramDestination.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import type { FieldErrors } from "../../lib/auth";
import { formatAge, utcTooltip } from "../../lib/format";
import {
  CHANNEL_KINDS,
  type ChannelDraft,
  channelDraftFrom,
  channelInput,
  describeDestination,
  destinationInput,
  emptyChannel,
  emptyDestination,
  kindLabel,
  normalizeDestinationField,
  validateChannel,
  validateDestination,
} from "../../lib/notifications";
import { serverFieldErrors } from "../../lib/projects";

/** The destination editor of each kind. */
const EDITORS: Record<ChannelKind, Component> = { email: EmailDestination, telegram: TelegramDestination };

const channels = useNotificationChannels();
const kinds = useChannelKinds();
const create = useCreateChannel();
const update = useUpdateChannel();
const remove = useDeleteChannel();
const tester = useTestChannel();

/** null: no form; "new": creating; otherwise the channel being edited. */
const editing = ref<string | null>(null);
const draft = ref<ChannelDraft>(emptyChannel());
const submitted = ref(false);
const serverErrors = ref<FieldErrors>({});
const formError = ref<string | null>(null);
const testResult = ref<{ ok: boolean; message: string } | null>(null);
const deleting = ref<string | null>(null);
const deleteError = ref<{ id: string; message: string } | null>(null);

const items = computed(() => channels.data.value?.items ?? []);
const current = computed(() => items.value.find((c) => c.channel_id === editing.value) ?? null);
const saving = computed(() => create.isPending.value || update.isPending.value);
const unavailable = computed(() => {
  const reasons: Partial<Record<ChannelKind, string>> = {};
  for (const k of kinds.data.value?.items ?? []) if (!k.available) reasons[k.kind] = k.reason ?? "Not configured on the server.";
  return reasons;
});
const kindReason = computed(() => unavailable.value[draft.value.destination.kind]);

const clientErrors = computed(() => validateChannel(draft.value));
const errors = computed<FieldErrors>(() => ({ ...(submitted.value ? clientErrors.value : {}), ...serverErrors.value }));
const errorId = (field: string) => `err-${field.replaceAll(".", "-")}`;
const describedBy = (field: string) => (errors.value[field] ? errorId(field) : undefined);

watch(draft, () => (serverErrors.value = {}), { deep: true });

function setKind(kind: ChannelKind) {
  if (kind === draft.value.destination.kind) return;
  draft.value.destination = emptyDestination(kind);
  testResult.value = null;
}

function serverErrorsOf(error: ApiError): FieldErrors {
  return Object.fromEntries(Object.entries(serverFieldErrors(error.problem)).map(([field, message]) => [normalizeDestinationField(field), message]));
}

function applyServerError(error: unknown) {
  if (error instanceof ApiError && error.problem.code === "validation_error") {
    serverErrors.value = serverErrorsOf(error);
    formError.value = "Some fields need attention.";
  } else if (error instanceof ApiError && error.problem.code === "channel_name_taken") {
    serverErrors.value = { name: error.problem.detail ?? "Name already in use" };
  } else {
    formError.value = error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : "Unexpected error";
  }
}

function open(channel: NotificationChannel | null) {
  editing.value = channel?.channel_id ?? "new";
  draft.value = channel ? channelDraftFrom(channel) : emptyChannel();
  submitted.value = false;
  formError.value = null;
  testResult.value = null;
}

function close() {
  editing.value = null;
  formError.value = null;
  serverErrors.value = {};
  testResult.value = null;
}

async function save() {
  submitted.value = true;
  formError.value = null;
  if (Object.keys(clientErrors.value).length) {
    formError.value = "Some fields need attention.";
    return;
  }
  const body = channelInput(draft.value);
  try {
    if (editing.value === "new") await create.mutateAsync(body);
    else if (editing.value) await update.mutateAsync({ id: editing.value, body });
    close();
  } catch (error) {
    applyServerError(error);
  }
}

async function sendTest() {
  testResult.value = null;
  const problems = validateDestination(draft.value.destination);
  if (Object.keys(problems).length) {
    serverErrors.value = Object.fromEntries(Object.entries(problems).map(([field, message]) => [`destination.${field}`, message]));
    return;
  }
  try {
    const result = await tester.mutateAsync({
      channel_id: editing.value === "new" ? null : editing.value,
      destination: destinationInput(draft.value.destination),
    });
    testResult.value = { ok: true, message: `Test message ${result.detail}.` };
  } catch (error) {
    if (error instanceof ApiError && error.problem.code === "validation_error") serverErrors.value = serverErrorsOf(error);
    testResult.value = { ok: false, message: error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : "Unexpected error" };
  }
}

async function confirmDelete(channel: NotificationChannel) {
  deleteError.value = null;
  try {
    await remove.mutateAsync(channel.channel_id);
    deleting.value = null;
    if (editing.value === channel.channel_id) close();
  } catch (error) {
    const message = error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : "Unexpected error";
    deleteError.value = { id: channel.channel_id, message };
  }
}
</script>

<template>
  <AppTopbar :crumbs="[{ label: 'Notification channels' }]" />
  <main class="page" aria-labelledby="channels-title">
    <div class="page-head">
      <h1 id="channels-title">Notification channels</h1>
      <button v-if="editing !== 'new'" type="button" class="primary" @click="open(null)">New channel</button>
    </div>
    <p class="muted">
      Where scheduled reports can be sent: an email list or a Telegram chat. Set a channel up once here; each project's schedule then picks the
      channels it sends to and when (after every run, or only on problems). Changing a channel applies to every project using it. Bot tokens are
      stored encrypted and never shown again.
    </p>

    <form v-if="editing" class="card form stack" novalidate aria-labelledby="channel-form-title" @submit.prevent="save">
      <h2 id="channel-form-title">{{ editing === "new" ? "New channel" : `Edit ${current?.name ?? "channel"}` }}</h2>
      <div class="field">
        <label for="channel-name">Name</label>
        <input id="channel-name" v-model="draft.name" maxlength="100" placeholder="Ops chat" :aria-invalid="!!errors.name" :aria-describedby="describedBy('name')">
        <p v-if="errors.name" :id="errorId('name')" class="field-error">{{ errors.name }}</p>
      </div>
      <div class="field">
        <label for="channel-description">Description <span class="muted">(optional)</span></label>
        <input id="channel-description" v-model="draft.description" maxlength="1000">
      </div>
      <div class="field">
        <label for="channel-kind">Type</label>
        <select id="channel-kind" :value="draft.destination.kind" @change="setKind(($event.target as HTMLSelectElement).value as ChannelKind)">
          <option v-for="k in CHANNEL_KINDS" :key="k" :value="k">{{ kindLabel(k) }}</option>
        </select>
      </div>
      <p v-if="kindReason" class="banner">{{ kindReason }} The channel can be saved, but nothing is sent until then.</p>
      <component
        :is="EDITORS[draft.destination.kind]"
        v-model="draft.destination"
        id-prefix="channel"
        field="destination"
        :errors="errors"
        v-bind="draft.destination.kind === 'telegram' ? { channelId: editing === 'new' ? null : editing } : {}"
      />
      <p v-if="formError" role="alert" class="banner error">{{ formError }}</p>
      <div class="row">
        <button type="submit" class="primary" :disabled="saving">{{ saving ? "Saving…" : editing === "new" ? "Create channel" : "Save changes" }}</button>
        <button type="button" :disabled="tester.isPending.value || !!kindReason" @click="sendTest">
          {{ tester.isPending.value ? "Sending…" : "Send test message" }}
        </button>
        <button type="button" @click="close">Cancel</button>
        <span v-if="testResult" role="status" :class="testResult.ok ? 'muted' : 'field-error'">{{ testResult.message }}</span>
      </div>
    </form>

    <div v-if="channels.isPending.value" aria-busy="true"><div class="skeleton" style="height: 88px" /></div>
    <div v-else-if="channels.isError.value" role="alert" class="banner error">
      <strong>Notification channels could not be loaded.</strong>
      {{ channels.error.value?.message }}
      <button type="button" @click="channels.refetch()">Retry</button>
    </div>
    <div v-else-if="items.length === 0" class="card empty">
      <p>No notification channels yet. Create one, then pick it in a project's schedule.</p>
    </div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr><th scope="col">Name</th><th scope="col">Destination</th><th scope="col">Used by</th><th scope="col">Updated</th><th scope="col"><span class="sr-only">Actions</span></th></tr>
        </thead>
        <tbody>
          <tr v-for="c in items" :key="c.channel_id">
            <th scope="row">
              {{ c.name }}
              <p v-if="c.description" class="muted hint">{{ c.description }}</p>
              <p v-if="!c.readable" class="field-error">Unreadable: the secret key changed. Enter the bot token again.</p>
            </th>
            <td>
              {{ describeDestination(c.destination) }}
              <p v-if="unavailable[c.destination.kind]" class="hint v-critical">{{ unavailable[c.destination.kind] }}</p>
            </td>
            <td>
              <span v-if="!c.used_by?.length" class="muted">Not used</span>
              <ul v-else class="used">
                <li v-for="u in c.used_by" :key="u.project_id"><RouterLink :to="`/projects/${u.project_id}`">{{ u.name }}</RouterLink></li>
              </ul>
            </td>
            <td><time :datetime="c.updated_at" :title="utcTooltip(c.updated_at)">{{ formatAge(c.updated_at) }}</time></td>
            <td class="actions">
              <div class="row">
                <button type="button" :aria-label="`Edit ${c.name}`" @click="open(c)">Edit</button>
                <button v-if="deleting !== c.channel_id" type="button" class="danger" :aria-label="`Delete ${c.name}`" @click="(deleting = c.channel_id), (deleteError = null)">
                  Delete…
                </button>
                <template v-else>
                  <button type="button" class="danger solid" :disabled="remove.isPending.value" @click="confirmDelete(c)">Delete permanently</button>
                  <button type="button" @click="deleting = null">Keep</button>
                </template>
              </div>
              <p v-if="deleteError?.id === c.channel_id" role="alert" class="field-error">{{ deleteError.message }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </main>
</template>

<style scoped>
.form { max-width: 720px; }
.form h2 { margin: 0; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.hint { font-size: 0.85rem; margin: 0; }
.used { list-style: none; margin: 0; padding: 0; }
.actions { white-space: nowrap; }
.actions .field-error { white-space: normal; max-width: 320px; }
</style>
