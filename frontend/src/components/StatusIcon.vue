<script setup lang="ts">
/** Coverage status glyphs from UI_SPEC §5: filled triangle, check, half circle, slashed circle, exclamation, dash. */
defineProps<{ status: string }>();
</script>

<template>
  <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true" class="icon">
    <path v-if="status === 'anomalous'" d="M7 1 13 12H1z" fill="currentColor" stroke="none" />
    <path v-else-if="status === 'no_anomaly'" d="M2 7.5 5.5 11 12 3.5" stroke-width="2" />
    <template v-else-if="status === 'insufficient_data'"><circle cx="7" cy="7" r="5.5" /><path d="M7 1.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" /></template>
    <template v-else-if="status === 'unsupported'"><circle cx="7" cy="7" r="5.5" /><path d="M3 11 11 3" /></template>
    <template v-else-if="status === 'source_error'"><circle cx="7" cy="7" r="5.5" /><path d="M7 4v3.5M7 10v.2" stroke-width="1.8" /></template>
    <path v-else d="M3.5 7h7" stroke-width="2" />
  </svg>
</template>

<style scoped>
.icon { flex-shrink: 0; }
</style>
