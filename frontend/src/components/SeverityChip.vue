<script setup lang="ts">
import { capitalize } from "../lib/format";

defineProps<{ severity: string }>();
</script>

<template>
  <span class="chip" :class="`sev-${severity}`">{{ capitalize(severity) }}</span>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;
}
.sev-critical { color: var(--sev-critical); background: var(--sev-critical-bg); }
.sev-high { color: var(--sev-high); background: var(--sev-high-bg); }
.sev-medium { color: var(--sev-medium); background: var(--sev-medium-bg); }
.sev-low { color: var(--sev-low); background: var(--sev-low-bg); }
</style>
