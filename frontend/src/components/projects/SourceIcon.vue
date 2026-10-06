<script setup lang="ts">
/** Source-kind glyphs: a flame for Prometheus-compatible metrics, a cloud for Cloudflare, a bug for Sentry, a shield for Wazuh. */
import type { SourceKind } from "../../api/client";

defineProps<{ kind: SourceKind }>();
</script>

<template>
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true" :class="['icon', kind]">
    <template v-if="kind === 'prometheus'">
      <path d="M8 1.5c.4 2.2 3.5 3.6 3.5 7a3.5 3.5 0 0 1-7 0c0-1.5.8-2.4 1.5-3 .1 1.2.6 1.9 1.3 2.2C7 6 7.4 3.6 8 1.5z" />
      <path d="M4 13.5h8" />
    </template>
    <template v-else-if="kind === 'sentry'">
      <rect x="4.5" y="5" width="7" height="8.5" rx="3.5" />
      <path d="M6 5a2 2 0 0 1 4 0M8 8v5.5M4.5 9H2M14 9h-2.5M4.8 6.5 3 5M11.2 6.5 13 5M4.8 12 3 13.5M11.2 12l1.8 1.5" />
    </template>
    <template v-else-if="kind === 'wazuh'">
      <path d="M8 1.5 13 3.5v4c0 3.2-2.1 5.6-5 7-2.9-1.4-5-3.8-5-7v-4z" />
      <path d="m5.75 8 1.6 1.6L10.5 6.4" />
    </template>
    <path v-else d="M4.5 12.5a3 3 0 0 1-.4-6A4 4 0 0 1 11.8 6 3.25 3.25 0 0 1 11.5 12.5z" />
  </svg>
</template>

<style scoped>
.icon { flex-shrink: 0; }
.prometheus { color: #e6522c; }
.cloudflare { color: #f38020; }
.sentry { color: #7553ff; }
.wazuh { color: #3595f9; }
</style>
