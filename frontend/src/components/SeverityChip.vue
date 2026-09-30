<script setup lang="ts">
defineProps<{ severity: string }>();
</script>

<template>
  <span class="chip" :class="`sev-${severity}`">{{ severity.charAt(0).toUpperCase() + severity.slice(1) }}</span>
</template>

<style scoped>
.chip {
  display: inline-block;
  padding: 0 calc(var(--space) * 0.75);
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  border: 1px solid currentColor;
  white-space: nowrap;
}
.sev-critical { color: var(--sev-critical); }
.sev-high { color: var(--sev-high); }
.sev-medium { color: var(--sev-medium); }
.sev-low { color: var(--sev-low); }
</style>
