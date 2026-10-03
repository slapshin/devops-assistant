<script setup lang="ts">
/** Source health of a stored project, fetched lazily (and cached) per project. */
import { computed } from "vue";
import type { ProjectSummary } from "../../api/client";
import { useProjectHealth } from "../../api/queries";
import { HEALTH_LABELS, healthKey } from "../../lib/projects";

const props = defineProps<{ project: ProjectSummary }>();

const checkable = computed(() => props.project.sources.length > 0 && props.project.credentials_readable);
const health = useProjectHealth(() => props.project.project_id, checkable);
const key = computed(() =>
  healthKey(props.project, health.data.value, { pending: health.isPending.value, failed: health.isError.value }),
);
const info = computed(() => HEALTH_LABELS[key.value]);
const detail = computed(() => {
  if (health.isError.value) return health.error.value?.message;
  const test = health.data.value;
  if (!test) return undefined;
  const series = test.matched_series !== null && test.matched_series !== undefined ? `${test.matched_series} matching series` : null;
  return [series, test.message].filter(Boolean).join(" · ") || undefined;
});
</script>

<template>
  <span class="health" :class="`h-${key}`" :title="detail" :aria-busy="key === 'checking'">
    <span aria-hidden="true">{{ info.icon }}</span> {{ info.label }}
  </span>
</template>

<style scoped>
.health { white-space: nowrap; font-weight: 500; font-size: 0.9rem; }
.h-ok { color: var(--status-ok); }
.h-unreachable, .h-auth, .h-unreadable, .h-error { color: var(--sev-critical); }
.h-no_series { color: var(--status-warn); }
.h-checking, .h-not_configured { color: var(--text-muted); }
</style>
