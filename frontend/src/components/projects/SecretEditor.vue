<script setup lang="ts">
/** The secret of a credential: the kind's mechanism (when it has several) and its fields. */
import { computed } from "vue";
import type { SourceKind } from "../../api/client";
import { CREDENTIAL_MECHANISMS, type FieldErrors, type SecretDraft, TOKEN_LABELS, keepsSecret } from "../../lib/auth";

const secret = defineModel<SecretDraft>({ required: true });
const props = defineProps<{
  kind: SourceKind;
  /** Prefix of the element ids, e.g. "credential" gives credential-token. */
  idPrefix: string;
  /** Prefix of the error keys, e.g. "auth" for "auth.token". */
  fieldPrefix: string;
  errors: FieldErrors;
}>();

const mechanisms = computed(() => CREDENTIAL_MECHANISMS[props.kind]);
const placeholder = computed(() => (keepsSecret(secret.value) ? "Stored — leave empty to keep" : ""));
const field = (name: string) => `${props.fieldPrefix}.${name}`;
const error = (name: string) => props.errors[field(name)] ?? (name !== "username" ? props.errors[props.fieldPrefix] : undefined);
const errorId = (name: string) => `err-${props.idPrefix}-${name}`;
const describedBy = (name: string) => (error(name) ? errorId(name) : undefined);
</script>

<template>
  <div class="secret stack">
    <div v-if="mechanisms.length > 1" class="field">
      <label :for="`${idPrefix}-mechanism`">Authentication</label>
      <select :id="`${idPrefix}-mechanism`" v-model="secret.type">
        <option v-for="m in mechanisms" :key="m.type" :value="m.type">{{ m.label }}</option>
      </select>
    </div>
    <div v-if="secret.type !== 'basic'" class="field">
      <label :for="`${idPrefix}-token`">{{ TOKEN_LABELS[kind] }}</label>
      <input
        :id="`${idPrefix}-token`"
        v-model="secret.token"
        type="password"
        autocomplete="new-password"
        :placeholder="placeholder"
        :aria-invalid="!!error('token')"
        :aria-describedby="describedBy('token')"
      >
      <p v-if="error('token')" :id="errorId('token')" class="field-error">{{ error("token") }}</p>
    </div>
    <template v-else>
      <div class="field">
        <label :for="`${idPrefix}-username`">Username</label>
        <input
          :id="`${idPrefix}-username`"
          v-model="secret.username"
          autocomplete="off"
          :aria-invalid="!!error('username')"
          :aria-describedby="describedBy('username')"
        >
        <p v-if="error('username')" :id="errorId('username')" class="field-error">{{ error("username") }}</p>
      </div>
      <div class="field">
        <label :for="`${idPrefix}-password`">Password</label>
        <input
          :id="`${idPrefix}-password`"
          v-model="secret.password"
          type="password"
          autocomplete="new-password"
          :placeholder="placeholder"
          :aria-invalid="!!error('password')"
          :aria-describedby="describedBy('password')"
        >
        <p v-if="error('password')" :id="errorId('password')" class="field-error">{{ error("password") }}</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
</style>
