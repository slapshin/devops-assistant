<script setup lang="ts">
/** Run button plus inline progress of the project's active analysis (the list polls while one is active). */
import { computed } from "vue";
import { ApiError, type ProjectSummary } from "../../api/client";
import { useSubmit } from "../../api/queries";
import { JOB_STATES, STAGE_LABELS } from "../../lib/format";

/** Shown when a queue_full response carries no Retry-After header. */
const DEFAULT_RETRY_AFTER_SECONDS = 30;

const props = defineProps<{ project: ProjectSummary }>();

const submit = useSubmit();
const active = computed(() => props.project.active_analysis ?? null);
const runnable = computed(() => props.project.sources.length > 0 && props.project.credentials_readable);
const blockedReason = computed(() => {
  if (props.project.sources.length === 0) return "Add a metrics source to run an analysis.";
  if (!props.project.credentials_readable) return "Re-enter the source credentials to run an analysis.";
  return null;
});
const stage = computed(() => {
  const running = active.value?.stages.find((s) => s.status === "running");
  if (!running) return null;
  const counts = running.total ? ` (${running.done ?? 0}/${running.total})` : "";
  return `${STAGE_LABELS[running.stage] ?? running.stage}${counts}`;
});
const error = computed(() => (submit.error.value instanceof ApiError ? submit.error.value : null));

async function run() {
  submit.reset();
  try {
    await submit.mutateAsync({ project_id: props.project.project_id });
  } catch {
    /* shown from submit.error */
  }
}
</script>

<template>
  <div class="run">
    <p v-if="active" class="progress" aria-live="polite">
      <span class="spinner" aria-hidden="true">◔</span>
      <RouterLink :to="`/analyses/${active.analysis_id}`">{{ JOB_STATES[active.state] ?? active.state }}</RouterLink>
      <span v-if="stage" class="muted"> · {{ stage }}</span>
    </p>
    <button
      v-else
      type="button"
      class="primary"
      :disabled="!runnable || submit.isPending.value"
      :aria-describedby="blockedReason ? `run-blocked-${project.project_id}` : undefined"
      @click="run"
    >
      {{ submit.isPending.value ? "Starting…" : "Run analysis" }}
    </button>
    <p v-if="blockedReason && !active" :id="`run-blocked-${project.project_id}`" class="muted hint">
      {{ blockedReason }}
      <RouterLink :to="`/projects/${project.project_id}/edit`">Edit</RouterLink>
    </p>
    <p v-if="error" role="alert" class="error-text">
      <template v-if="error.problem.code === 'queue_full'">
        The analysis queue is full. Try again in {{ error.retryAfter ?? DEFAULT_RETRY_AFTER_SECONDS }} seconds.
      </template>
      <template v-else>{{ error.problem.title }}<span v-if="error.problem.detail">: {{ error.problem.detail }}</span></template>
    </p>
  </div>
</template>

<style scoped>
.run { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; text-align: right; }
.progress { margin: 0; }
.spinner { display: inline-block; margin-right: 4px; color: var(--primary); }
.hint, .error-text { margin: 0; font-size: 0.85rem; max-width: 22em; }
.error-text { color: var(--crit); }
@media (max-width: 720px) {
  .run { align-items: flex-start; text-align: left; }
}
</style>
