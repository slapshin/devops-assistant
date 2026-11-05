<script setup lang="ts">
/** Channel-kind glyphs: an envelope for email, a paper plane for Telegram. */
import type { ChannelKind } from "../../api/client";

defineProps<{ kind: ChannelKind }>();
</script>

<template>
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true" :class="['icon', kind]">
    <template v-if="kind === 'telegram'">
      <path d="M14.5 2 1.5 7.2l4.3 1.6L12 4.5 7.3 9.9v3.6l2.3-2.6 3 2.1z" />
    </template>
    <template v-else>
      <rect x="1.75" y="3.5" width="12.5" height="9" rx="1.5" />
      <path d="m2.25 4.25 5.75 4.5 5.75-4.5" />
    </template>
  </svg>
</template>

<style scoped>
.icon { flex-shrink: 0; vertical-align: -3px; }
.telegram { color: #26a5e4; }
.email { color: var(--muted); }
</style>
