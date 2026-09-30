<script setup lang="ts">
import { computed } from "vue";
import { STATUS_LABELS } from "../lib/format";

const props = defineProps<{ status: string }>();
const info = computed(() => STATUS_LABELS[props.status] ?? { label: props.status, icon: "?" });
</script>

<template>
  <span class="status" :class="`st-${status}`"><span aria-hidden="true">{{ info.icon }}</span> {{ info.label }}</span>
</template>

<style scoped>
.status { white-space: nowrap; font-weight: 500; }
.st-anomalous { color: var(--sev-high); }
.st-no_anomaly { color: var(--status-ok); }
.st-insufficient_data, .st-source_error { color: var(--status-warn); }
.st-unsupported, .st-not_evaluated { color: var(--text-muted); }
</style>
