<script setup lang="ts">
/** A source's credential: one of its kind's credentials, none where the kind allows it, or a new one created in place. */
import { computed, ref } from "vue";
import { ApiError, type SourceKind } from "../../api/client";
import { useCreateCredential, useCredentials } from "../../api/queries";
import { type FieldErrors, type SecretDraft, emptySecret, secretInput, validateSecret } from "../../lib/auth";
import { SOURCE_KIND_LABELS, serverFieldErrors } from "../../lib/projects";
import SecretEditor from "./SecretEditor.vue";

const credentialId = defineModel<string>({ required: true });
const props = defineProps<{
  kind: SourceKind;
  /** Prefix of the element ids, e.g. "cf" gives cf-credential. */
  idPrefix: string;
  errors: FieldErrors;
  /** The source works without authentication (e.g. synthetic:// or an open indexer). */
  optional: boolean;
}>();

const credentials = useCredentials();
const creator = useCreateCredential();
const creating = ref(false);
const name = ref("");
const secret = ref<SecretDraft>(emptySecret(props.kind));
const createErrors = ref<FieldErrors>({});
const createError = ref<string | null>(null);

const usable = computed(() => (credentials.data.value?.items ?? []).filter((c) => c.kind === props.kind));
const field = `${props.kind}.auth.credential_id`;
/** "Choose a credential" from the client, or the server's message about this source's auth. */
const error = computed(() => props.errors[field] ?? props.errors[`${props.kind}.auth`]);
const errorId = `err-${props.idPrefix}-credential`;

function startCreate() {
  creating.value = true;
  name.value = "";
  secret.value = emptySecret(props.kind);
  createErrors.value = {};
  createError.value = null;
}

async function create() {
  const trimmed = name.value.trim();
  createErrors.value = { ...(trimmed ? {} : { name: "Required" }), ...validateSecret(secret.value, "auth") };
  createError.value = null;
  if (Object.keys(createErrors.value).length) return;
  try {
    const created = await creator.mutateAsync({ name: trimmed, kind: props.kind, auth: secretInput(secret.value) });
    credentialId.value = created.credential_id;
    creating.value = false;
  } catch (e) {
    if (e instanceof ApiError && e.problem.code === "validation_error") createErrors.value = serverFieldErrors(e.problem);
    else if (e instanceof ApiError && e.problem.code === "credential_name_taken") createErrors.value = { name: e.problem.detail ?? "Name already in use" };
    else createError.value = e instanceof ApiError ? (e.problem.detail ?? e.problem.title) : "Unexpected error";
  }
}
</script>

<template>
  <div class="picker stack">
    <div class="field">
      <label :for="`${idPrefix}-credential`">Credential</label>
      <div class="row">
        <select
          :id="`${idPrefix}-credential`"
          v-model="credentialId"
          :aria-invalid="!!error"
          :aria-describedby="[error ? errorId : '', $slots.hint ? `${idPrefix}-credential-hint` : ''].filter(Boolean).join(' ') || undefined"
        >
          <option value="" :disabled="!optional">{{ optional ? "None" : "Choose a credential…" }}</option>
          <option v-for="c in usable" :key="c.credential_id" :value="c.credential_id">{{ c.name }}</option>
        </select>
        <button v-if="!creating" type="button" @click="startCreate">New credential…</button>
      </div>
      <p v-if="$slots.hint" :id="`${idPrefix}-credential-hint`" class="muted hint"><slot name="hint" /></p>
      <p v-if="error" :id="errorId" class="field-error">{{ error }}</p>
    </div>

    <div v-if="creating" class="create stack" role="group" :aria-label="`New ${SOURCE_KIND_LABELS[kind]} credential`">
      <div class="field">
        <label :for="`${idPrefix}-new-name`">Credential name</label>
        <input
          :id="`${idPrefix}-new-name`"
          v-model="name"
          maxlength="100"
          autocomplete="off"
          :aria-invalid="!!createErrors.name"
          :aria-describedby="createErrors.name ? `err-${idPrefix}-new-name` : undefined"
        >
        <p v-if="createErrors.name" :id="`err-${idPrefix}-new-name`" class="field-error">{{ createErrors.name }}</p>
      </div>
      <SecretEditor v-model="secret" :kind="kind" :id-prefix="`${idPrefix}-new`" field-prefix="auth" :errors="createErrors" />
      <p class="muted hint">Other projects can use this credential too; manage it under <RouterLink to="/credentials">Credentials</RouterLink>.</p>
      <p v-if="createError" role="alert" class="field-error">{{ createError }}</p>
      <div class="row">
        <button type="button" class="primary" :disabled="creator.isPending.value" @click="create">
          {{ creator.isPending.value ? "Creating…" : "Create credential" }}
        </button>
        <button type="button" @click="creating = false">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.field select { flex: 1 1 240px; }
.hint { font-size: 0.85rem; margin: 0; }
.create { border-left: 3px solid var(--border); padding-left: calc(var(--space) * 1.5); }
</style>
