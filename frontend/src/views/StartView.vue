<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { useAnalyses, useProjects, useSubmit } from "../api/queries";
import { JOB_STATES, SEVERITIES, formatTime, utcTooltip } from "../lib/format";

const LAST_PROJECT_STORAGE_KEY = "assistant.lastProject";
/** Shown when a queue_full response carries no Retry-After header. */
const DEFAULT_RETRY_AFTER_SECONDS = 30;

const route = useRoute();
const router = useRouter();

const projectId = ref<string | null>((route.query.project as string) || null);
const projects = useProjects();
const analyses = useAnalyses(projectId);
const submit = useSubmit();

const selected = computed(() => projects.data.value?.items.find((p) => p.project_id === projectId.value) ?? null);
const canAnalyze = computed(() => !!selected.value && !submit.isPending.value);
const submitError = computed(() => (submit.error.value instanceof ApiError ? submit.error.value : null));

function readLastProject(): string | null {
  try {
    return localStorage.getItem(LAST_PROJECT_STORAGE_KEY);
  } catch {
    return null;
  }
}

function rememberProject(id: string) {
  try {
    localStorage.setItem(LAST_PROJECT_STORAGE_KEY, id);
  } catch {
    /* storage unavailable: the project is just not remembered */
  }
}

const reportLink = (id: string, available: boolean) => (available ? `/reports/${id}` : `/analyses/${id}`);

function selectProject(value: string) {
  projectId.value = value || null;
  submit.reset();
}

async function analyze() {
  if (!selected.value) return;

  rememberProject(selected.value.project_id);

  let result;
  try {
    result = await submit.mutateAsync({ project_id: selected.value.project_id });
  } catch {
    return; // shown from submit.error
  }

  await router.push({
    path: `/analyses/${result.analysis.analysis_id}`,
    query: result.duplicate_of_active ? { duplicate: "1" } : {},
  });
}

// Reapply the last project only if it still exists.
watch(
  () => projects.data.value,
  (data) => {
    if (!data || projectId.value) return;

    const last = readLastProject();
    if (last && data.items.some((p) => p.project_id === last)) projectId.value = last;
    else if (data.items.length === 1) projectId.value = data.items[0]?.project_id ?? null;
  },
  { immediate: true },
);
</script>

<template>
  <section aria-labelledby="start-title" class="stack">
    <h1 id="start-title">Analyse a project</h1>
    <p class="muted">Anomalies in the latest 24 hours, compared with up to 14 preceding days, plus a 14-day trend.</p>

    <div v-if="projects.isPending.value" aria-busy="true"><div class="skeleton" style="width: 40%" /></div>
    <div v-else-if="projects.isError.value" role="alert" class="banner error">
      <strong>Projects could not be loaded.</strong>
      {{ projects.error.value?.message }}
      <button type="button" @click="projects.refetch()">Retry</button>
    </div>
    <div v-else-if="projects.data.value && projects.data.value.items.length === 0" class="banner">
      No projects yet. Create one with <code>POST /api/projects</code>.
    </div>

    <form v-else class="row selectors" @submit.prevent="analyze">
      <label>
        Project
        <select :value="projectId ?? ''" @change="selectProject(($event.target as HTMLSelectElement).value)">
          <option value="" disabled>Select a project</option>
          <option v-for="p in projects.data.value?.items" :key="p.project_id" :value="p.project_id">{{ p.name }}</option>
        </select>
      </label>
      <button type="submit" class="primary" :disabled="!canAnalyze">
        {{ submit.isPending.value ? "Starting…" : "Analyze" }}
      </button>
    </form>

    <p v-if="submitError" role="alert" class="banner error">
      <template v-if="submitError.problem.code === 'queue_full'">
        The analysis queue is full. Try again in {{ submitError.retryAfter ?? DEFAULT_RETRY_AFTER_SECONDS }} seconds.
      </template>
      <template v-else>{{ submitError.problem.title }}<span v-if="submitError.problem.detail">: {{ submitError.problem.detail }}</span></template>
    </p>

    <template v-if="selected">
      <h2>Recent reports for {{ selected.name }}</h2>
      <div v-if="analyses.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>
      <p v-else-if="analyses.data.value?.items.length === 0" class="muted">No analyses yet for this project.</p>
      <div v-else-if="analyses.data.value" class="table-wrap">
        <table class="table">
          <thead>
            <tr><th scope="col">Window end</th><th scope="col">State</th><th scope="col">Findings</th><th scope="col">AI</th></tr>
          </thead>
          <tbody>
            <tr v-for="job in analyses.data.value.items" :key="job.analysis_id">
              <td>
                <RouterLink :to="reportLink(job.analysis_id, job.report_available)" :title="utcTooltip(job.end_time)">
                  {{ formatTime(job.end_time) }}
                </RouterLink>
              </td>
              <td>{{ JOB_STATES[job.state] ?? job.state }}<span v-if="job.error" class="muted"> · {{ job.error.code }}</span></td>
              <td>
                <span v-if="job.finding_counts" class="sev-counts">
                  <template v-for="s in SEVERITIES" :key="s">
                    <span v-if="job.finding_counts[s]" :class="`sev-text-${s}`">{{ job.finding_counts[s] }} {{ s }}</span>
                  </template>
                  <span v-if="!Object.values(job.finding_counts).some(Boolean)" class="muted">none</span>
                </span>
                <span v-else class="muted">—</span>
              </td>
              <td>{{ job.explanation_status.replaceAll("_", " ") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.selectors label { display: flex; flex-direction: column; gap: 4px; font-weight: 500; }
.selectors { align-items: flex-end; }
.sev-counts { display: inline-flex; flex-wrap: wrap; gap: 0 0.75em; }
.sev-text-critical { color: var(--sev-critical); }
.sev-text-high { color: var(--sev-high); }
.sev-text-medium { color: var(--sev-medium); }
.sev-text-low { color: var(--sev-low); }
</style>
