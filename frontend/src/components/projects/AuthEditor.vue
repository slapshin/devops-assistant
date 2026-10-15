<script setup lang="ts">
/** Authentication of one source (or of a shared credential): the kind's own mechanisms, or a shared credential of that kind. */
import { computed, ref } from "vue";
import { ApiError, type SourceKind } from "../../api/client";
import { useCreateCredential, useCredentials } from "../../api/queries";
import { AUTH_MECHANISMS, type AuthDraft, CREDENTIAL_MECHANISMS, type FieldErrors, TOKEN_LABELS, credentialAuthInput, keepsSecret } from "../../lib/auth";

const auth = defineModel<AuthDraft>({ required: true });
const props = withDefaults(
  defineProps<{
    kind: SourceKind;
    /** Prefix of the element ids, e.g. "cf" gives cf-auth, cf-token. */
    idPrefix: string;
    errors: FieldErrors;
    /** A shared credential's own auth: only secret mechanisms, no credential choice. */
    forCredential?: boolean;
  }>(),
  { forCredential: false },
);

const credentials = useCredentials();
const creator = useCreateCredential();
const sharing = ref(false);
const shareName = ref("");
const shareError = ref<string | null>(null);

const usable = computed(() => (credentials.data.value?.items ?? []).filter((c) => c.kind === props.kind));
const options = computed(() => {
  const own = (props.forCredential ? CREDENTIAL_MECHANISMS : AUTH_MECHANISMS)[props.kind];
  const shared = !props.forCredential && (usable.value.length > 0 || auth.value.type === "credential");
  return shared ? [...own, { type: "credential" as const, label: "Shared credential" }] : own;
});
const keeps = computed(() => keepsSecret(auth.value));
const placeholder = computed(() => (keeps.value ? "Stored — leave empty to keep" : ""));
const field = (name?: string) => (name ? `${props.kind}.auth.${name}` : `${props.kind}.auth`);
/** "auth is required" from the server belongs to the secret input. */
const error = (name: string) => props.errors[field(name)] ?? (name === "token" || name === "password" ? props.errors[field()] : undefined);
const errorId = (name: string) => `err-${field(name).replaceAll(".", "-")}`;
const describedBy = (name: string, hint?: string) => (error(name) ? errorId(name) : hint);
const secret = computed(() => (auth.value.type === "basic" ? auth.value.password : auth.value.token));
const canShare = computed(() => !props.forCredential && auth.value.type !== "none" && auth.value.type !== "credential" && !!secret.value);

async function share() {
  shareError.value = null;
  const name = shareName.value.trim();
  if (!name) {
    shareError.value = "Required";
    return;
  }
  try {
    const created = await creator.mutateAsync({ name, kind: props.kind, auth: credentialAuthInput(auth.value) });
    auth.value = { ...auth.value, type: "credential", credentialId: created.credential_id, token: "", password: "" };
    sharing.value = false;
    shareName.value = "";
  } catch (e) {
    shareError.value = e instanceof ApiError ? (e.problem.detail ?? e.problem.title) : "Unexpected error";
  }
}
</script>

<template>
  <div class="auth stack">
    <div v-if="options.length > 1" class="field">
      <label :for="`${idPrefix}-auth`">Authentication</label>
      <select :id="`${idPrefix}-auth`" v-model="auth.type">
        <option v-for="o in options" :key="o.type" :value="o.type">{{ o.label }}</option>
      </select>
    </div>

    <div v-if="auth.type === 'credential'" class="field">
      <label :for="`${idPrefix}-credential`">Shared credential</label>
      <select
        :id="`${idPrefix}-credential`"
        v-model="auth.credentialId"
        :aria-invalid="!!error('credential_id')"
        :aria-describedby="describedBy('credential_id', `${idPrefix}-credential-hint`)"
      >
        <option value="" disabled>Choose…</option>
        <option v-for="c in usable" :key="c.credential_id" :value="c.credential_id">{{ c.name }}</option>
      </select>
      <p :id="`${idPrefix}-credential-hint`" class="muted hint">
        Rotating a shared credential updates every project using it. <RouterLink to="/credentials">Manage shared credentials</RouterLink>
      </p>
      <p v-if="error('credential_id')" :id="errorId('credential_id')" class="field-error">{{ error("credential_id") }}</p>
    </div>

    <div v-if="auth.type === 'bearer' || auth.type === 'token'" class="field">
      <label :for="`${idPrefix}-token`">{{ TOKEN_LABELS[kind] }}</label>
      <input
        :id="`${idPrefix}-token`"
        v-model="auth.token"
        type="password"
        autocomplete="new-password"
        :placeholder="placeholder"
        :aria-invalid="!!error('token')"
        :aria-describedby="describedBy('token', $slots.hint ? `${idPrefix}-token-hint` : undefined)"
      >
      <p v-if="$slots.hint" :id="`${idPrefix}-token-hint`" class="muted hint"><slot name="hint" /></p>
      <p v-if="error('token')" :id="errorId('token')" class="field-error">{{ error("token") }}</p>
    </div>

    <template v-if="auth.type === 'basic'">
      <div class="field">
        <label :for="`${idPrefix}-username`">Username</label>
        <input
          :id="`${idPrefix}-username`"
          v-model="auth.username"
          autocomplete="off"
          :aria-invalid="!!error('username')"
          :aria-describedby="describedBy('username', $slots.hint ? `${idPrefix}-username-hint` : undefined)"
        >
        <p v-if="$slots.hint" :id="`${idPrefix}-username-hint`" class="muted hint"><slot name="hint" /></p>
        <p v-if="error('username')" :id="errorId('username')" class="field-error">{{ error("username") }}</p>
      </div>
      <div class="field">
        <label :for="`${idPrefix}-password`">Password</label>
        <input
          :id="`${idPrefix}-password`"
          v-model="auth.password"
          type="password"
          autocomplete="new-password"
          :placeholder="placeholder"
          :aria-invalid="!!error('password')"
          :aria-describedby="describedBy('password')"
        >
        <p v-if="error('password')" :id="errorId('password')" class="field-error">{{ error("password") }}</p>
      </div>
    </template>

    <div v-if="canShare" class="share">
      <button v-if="!sharing" type="button" class="link" @click="sharing = true">Save as shared credential…</button>
      <div v-else class="row">
        <label :for="`${idPrefix}-share-name`" class="sr-only">Shared credential name</label>
        <input
          :id="`${idPrefix}-share-name`"
          v-model="shareName"
          maxlength="100"
          placeholder="Credential name"
          :aria-invalid="!!shareError"
          :aria-describedby="shareError ? `${idPrefix}-share-error` : undefined"
          @keydown.enter.prevent="share"
        >
        <button type="button" :disabled="creator.isPending.value" @click="share">Save</button>
        <button type="button" @click="sharing = false">Cancel</button>
        <p v-if="shareError" :id="`${idPrefix}-share-error`" class="field-error">{{ shareError }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.hint { font-size: 0.85rem; margin: 0; }
.share input { flex: 1 1 200px; }
.share .field-error { flex-basis: 100%; }
</style>
