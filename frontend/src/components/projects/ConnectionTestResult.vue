<script setup lang="ts">
import { computed } from "vue";
import type { ConnectionTest } from "../../api/client";
import { CAPABILITY_LABELS, FAMILY_LABELS, formatTime, utcTooltip } from "../../lib/format";
import { HEALTH_LABELS, testKey } from "../../lib/projects";

const props = defineProps<{ test: ConnectionTest; stale?: boolean }>();

const key = computed(() => testKey(props.test));
const info = computed(() => HEALTH_LABELS[key.value]);
</script>

<template>
  <section class="result card" aria-label="Connection test result" aria-live="polite">
    <p class="summary">
      <strong :class="`h-${key}`"><span aria-hidden="true">{{ info.icon }}</span> {{ info.label }}</strong>
      <span v-if="test.matched_series !== null && test.matched_series !== undefined"> · {{ test.matched_series }} matching series</span>
      <span v-if="test.history_days !== null && test.history_days !== undefined"> · {{ test.history_days }} days of history</span>
      <span class="muted" :title="utcTooltip(test.checked_at)"> · checked {{ formatTime(test.checked_at) }}</span>
    </p>
    <p v-if="stale" class="muted">The form changed after this test; run it again to check the current values.</p>
    <p v-if="test.message" class="muted">{{ test.message }}</p>
    <div v-if="test.families?.length" class="table-wrap">
      <table class="table">
        <thead><tr><th scope="col">Signal family</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
        <tbody>
          <tr v-for="f in test.families" :key="f.family">
            <td>{{ FAMILY_LABELS[f.family] ?? f.family }}</td>
            <td :class="`cap-${f.status}`">
              <span aria-hidden="true">{{ CAPABILITY_LABELS[f.status]?.icon }}</span> {{ CAPABILITY_LABELS[f.status]?.label ?? f.status }}
            </td>
            <td class="muted">{{ f.reason ?? "" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.result p { margin: 0 0 var(--space); }
.h-ok, .cap-supported { color: var(--ok); }
.h-unreachable, .h-auth, .h-error { color: var(--crit); }
.h-no_series, .cap-partial { color: var(--med); }
.cap-unsupported, .cap-unverified { color: var(--muted); }
</style>
