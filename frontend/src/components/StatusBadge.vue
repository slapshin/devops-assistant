<script setup lang="ts">
import { computed } from "vue";
import { STATUS_LABELS } from "../lib/format";
import StatusIcon from "./StatusIcon.vue";

const props = defineProps<{ status: string }>();
const label = computed(() => STATUS_LABELS[props.status]?.label ?? props.status);
</script>

<template>
  <span class="status" :class="`st-${status}`"><StatusIcon :status="status" /> {{ label }}</span>
</template>

<style scoped>
.status { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-weight: 500; }
.st-anomalous { color: var(--crit); }
.st-no_anomaly { color: var(--ok); }
.st-insufficient_data, .st-source_error, .st-unsupported { color: var(--med); }
.st-not_evaluated { color: var(--muted); }
</style>
