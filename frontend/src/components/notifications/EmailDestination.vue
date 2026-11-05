<script setup lang="ts">
/** Recipients of an email notification channel. */
import type { FieldErrors } from "../../lib/auth";
import { type EmailDraft, MAX_RECIPIENTS } from "../../lib/notifications";

const destination = defineModel<EmailDraft>({ required: true });
const props = defineProps<{
  /** Prefix of the element ids. */
  idPrefix: string;
  /** Prefix of the error keys, e.g. "destination". */
  field: string;
  errors: FieldErrors;
}>();

const error = () => props.errors[`${props.field}.recipients`];
</script>

<template>
  <div class="field">
    <label :for="`${idPrefix}-recipients`">Recipients</label>
    <textarea
      :id="`${idPrefix}-recipients`"
      v-model="destination.recipients"
      rows="2"
      autocomplete="email"
      placeholder="oncall@example.com, team@example.com"
      :aria-invalid="!!error()"
      :aria-describedby="error() ? `err-${idPrefix}-recipients` : `${idPrefix}-recipients-hint`"
    />
    <p :id="`${idPrefix}-recipients-hint`" class="muted hint">Up to {{ MAX_RECIPIENTS }} addresses, separated by commas or new lines.</p>
    <p v-if="error()" :id="`err-${idPrefix}-recipients`" class="field-error">{{ error() }}</p>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.hint { font-size: 0.85rem; margin: 0; }
</style>
