<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { isActive, useAnalysis, useCancel, useSubmit } from "../api/queries";
import AppTopbar from "../components/shell/AppTopbar.vue";
import type { Crumb } from "../components/shell/crumbs";
import { JOB_STATES, STAGE_LABELS, formatTime, utcTooltip } from "../lib/format";

const STAGE_STATUS_ICONS: Record<string, string> = { pending: "○", running: "◔", done: "●", failed: "✕", skipped: "–" };

const props = defineProps<{ id: string }>();

const route = useRoute();
const router = useRouter();
const job = useAnalysis(() => props.id);
const cancel = useCancel();
const submit = useSubmit();
const confirming = ref(false);

const current = computed(() => job.data.value);
const active = computed(() => !!current.value && isActive(current.value.state));
const runningStage = computed(() => current.value?.stages.find((s) => s.status === "running")?.stage ?? null);
const lostConnection = computed(() => job.failureCount.value >= 1 && job.isError.value === false && job.isFetching.value);
const notFound = computed(() => job.error.value instanceof ApiError && job.error.value.problem.status === 404);
const crumbs = computed<Crumb[]>(() => {
  const scope = current.value?.scope;
  return [
    { label: "Projects", to: "/" },
    ...(scope ? [{ label: scope.project_name, to: `/projects/${scope.project_id}`, mono: true }] : []),
    { label: "Analysis progress" },
  ];
});

async function confirmCancel() {
  confirming.value = false;
  await cancel.mutateAsync(props.id);
}

async function runAgain() {
  if (!current.value) return;

  try {
    const submitted = await submit.mutateAsync({ project_id: current.value.scope.project_id });
    await router.push(`/analyses/${submitted.analysis.analysis_id}`);
  } catch {
    /* shown from submit.error */
  }
}

watch(
  () => current.value,
  (currentJob) => {
    if (currentJob?.report_available) void router.replace(`/reports/${currentJob.analysis_id}`);
  },
  { immediate: true },
);
</script>

<template>
  <AppTopbar :crumbs="crumbs" />
  <main class="page" aria-labelledby="job-title">
    <h1 id="job-title">Analysis progress</h1>
    <p v-if="route.query.duplicate" class="banner">An analysis for this scope is already running; showing it.</p>

    <p v-if="notFound" role="alert" class="banner error">This analysis does not exist. <RouterLink to="/">Back to projects</RouterLink>.</p>
    <p v-else-if="job.isError.value" role="alert" class="banner error">
      Lost connection to the assistant — retrying failed. <button type="button" @click="job.refetch()">Retry</button>
    </p>
    <div v-else-if="job.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>

    <template v-else-if="current">
      <p class="scope">
        <RouterLink :to="`/projects/${current.scope.project_id}`" class="mono">{{ current.scope.project_name }}</RouterLink>
        · window ends <span :title="utcTooltip(current.end_time)">{{ formatTime(current.end_time) }}</span>
        · <span class="tag">{{ JOB_STATES[current.state] }}</span>
      </p>
      <p v-if="lostConnection" class="muted">Lost connection to the assistant — retrying…</p>

      <ol class="stages panel" aria-live="polite" :aria-busy="active">
        <li v-for="s in current.stages" :key="s.stage" :class="`s-${s.status}`">
          <span aria-hidden="true">{{ STAGE_STATUS_ICONS[s.status] }}</span>
          {{ STAGE_LABELS[s.stage] ?? s.stage }} — <span class="st">{{ s.status }}</span>
          <span v-if="s.total" class="muted"> ({{ s.done ?? 0 }}/{{ s.total }})</span>
          <span v-if="s.message" class="muted"> · {{ s.message }}</span>
        </li>
      </ol>
      <p class="sr-only" aria-live="polite">{{ runningStage ? `Now: ${STAGE_LABELS[runningStage]}` : "" }}</p>

      <div v-if="active" class="row">
        <button v-if="!confirming" type="button" @click="confirming = true">Cancel analysis</button>
        <template v-else>
          <span>Cancel this analysis? No report will be saved.</span>
          <button type="button" class="primary" @click="confirmCancel">Yes, cancel</button>
          <button type="button" @click="confirming = false">Keep running</button>
        </template>
      </div>

      <div v-if="current.state === 'failed'" role="alert" class="banner error">
        <strong v-if="current.error?.code === 'interrupted_by_restart'">The service restarted while this analysis was running. No report was saved.</strong>
        <template v-else>
          <strong>Analysis failed ({{ current.error?.code }}).</strong> {{ current.error?.message }}
        </template>
      </div>
      <p v-if="current.state === 'cancelled'" class="banner">This analysis was cancelled. No report was saved.</p>
      <p v-if="submit.error.value" role="alert" class="banner error">Could not start a new analysis: {{ submit.error.value.message }}</p>
      <div v-if="!active && !current.report_available" class="row">
        <button type="button" class="primary" :disabled="submit.isPending.value" @click="runAgain">Run again</button>
        <RouterLink :to="`/projects/${current.scope.project_id}`">Back to project</RouterLink>
      </div>
    </template>
  </main>
</template>

<style scoped>
.scope { margin: 0; }
.stages { list-style: none; margin: 0; padding: 4px 12px; max-width: 720px; }
.stages li { padding: 8px 0; border-bottom: 1px solid var(--grid); }
.stages li:last-child { border-bottom: none; }
.s-done { color: var(--ok); }
.s-running { font-weight: 600; }
.s-failed { color: var(--crit); }
.s-pending, .s-skipped { color: var(--muted); }
.st { text-transform: capitalize; }
</style>
