<script setup lang="ts">
/** Health of one source of a stored project as a status glyph; the label and test detail are in the tooltip. */
import { computed } from "vue";
import type { ProjectSummary, SourceKind } from "../../api/client";
import { useProjectHealth } from "../../api/queries";
import { HEALTH_LABELS, testKey, testVolume, type HealthKey } from "../../lib/projects";
import StatusIcon from "../StatusIcon.vue";

const props = defineProps<{ project: ProjectSummary; kind: SourceKind }>();

/** Coverage glyphs reused for health: check, half circle, exclamation, dash. */
const GLYPHS: Record<HealthKey, string> = {
  ok: "no_anomaly",
  no_series: "insufficient_data",
  no_traffic: "insufficient_data",
  unreachable: "source_error",
  auth: "source_error",
  unreadable: "source_error",
  error: "source_error",
  checking: "",
};

const checkable = computed(() => props.project.credentials_readable);
const health = useProjectHealth(() => props.project.project_id, checkable);
const test = computed(() => health.data.value?.find((t) => (t.kind ?? "prometheus") === props.kind));
const key = computed<HealthKey>(() => {
  if (!props.project.credentials_readable) return "unreadable";
  if (health.isError.value) return "error";
  return test.value ? testKey(test.value) : "checking";
});
const label = computed(() => HEALTH_LABELS[key.value].label);
const detail = computed(() => {
  if (health.isError.value) return [label.value, health.error.value?.message].filter(Boolean).join(" · ");
  return [label.value, test.value && testVolume(test.value), test.value?.message].filter(Boolean).join(" · ");
});
</script>

<template>
  <span class="health" :class="`h-${key}`" role="img" :aria-label="`Health: ${label}`" :title="detail" :aria-busy="key === 'checking'">
    <StatusIcon :status="GLYPHS[key]" />
  </span>
</template>

<style scoped>
.health { display: inline-flex; align-items: center; vertical-align: -1px; cursor: help; }
.h-ok { color: var(--ok); }
.h-unreachable, .h-auth, .h-unreadable, .h-error { color: var(--crit); }
.h-no_series, .h-no_traffic { color: var(--med); }
.h-checking { color: var(--muted); }
</style>
