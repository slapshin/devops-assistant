<script setup lang="ts">
import { SEVERITIES } from "../lib/format";

defineProps<{ counts: Partial<Record<string, number>> | null | undefined }>();
</script>

<template>
  <span v-if="counts" class="sev-counts">
    <template v-for="s in SEVERITIES" :key="s">
      <span v-if="counts[s]" :class="`sev-text-${s}`">{{ counts[s] }} {{ s }}</span>
    </template>
    <span v-if="!Object.values(counts).some(Boolean)" class="muted">no findings</span>
  </span>
  <span v-else class="muted">—</span>
</template>

<style scoped>
.sev-counts { display: inline-flex; flex-wrap: wrap; gap: 0 0.75em; }
.sev-text-critical { color: var(--sev-critical); }
.sev-text-high { color: var(--sev-high); }
.sev-text-medium { color: var(--sev-medium); }
.sev-text-low { color: var(--sev-low); }
</style>
