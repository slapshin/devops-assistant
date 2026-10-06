<script setup lang="ts">
/** Source health of a stored project, fetched lazily (and cached) per project. */
import { computed } from "vue";
import type { ProjectSummary } from "../../api/client";
import { useProjectHealth } from "../../api/queries";
import { HEALTH_LABELS, healthKey, sourceKindLabel, testKey, testVolume } from "../../lib/projects";

const props = defineProps<{ project: ProjectSummary }>();

const checkable = computed(() => props.project.sources.length > 0 && props.project.credentials_readable);
const health = useProjectHealth(() => props.project.project_id, checkable);
const key = computed(() =>
  healthKey(props.project, health.data.value, { pending: health.isPending.value, failed: health.isError.value }),
);
const info = computed(() => HEALTH_LABELS[key.value]);
const detail = computed(() => {
  if (health.isError.value) return health.error.value?.message;
  const tests = health.data.value ?? [];
  const lines = tests.map((test) => {
    const status = HEALTH_LABELS[testKey(test)].label;
    return [`${sourceKindLabel(test.kind ?? "prometheus")}: ${status}`, testVolume(test), test.message].filter(Boolean).join(" · ");
  });
  return lines.join("\n") || undefined;
});
</script>

<template>
  <span class="health" :class="`h-${key}`" :title="detail" :aria-busy="key === 'checking'">
    <span aria-hidden="true">{{ info.icon }}</span> {{ info.label }}
  </span>
</template>

<style scoped>
.health { white-space: nowrap; font-weight: 500; font-size: 0.9rem; }
.h-ok { color: var(--ok); }
.h-unreachable, .h-auth, .h-unreadable, .h-error { color: var(--crit); }
.h-no_series, .h-no_traffic { color: var(--med); }
.h-checking, .h-not_configured { color: var(--muted); }
</style>
