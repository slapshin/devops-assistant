<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { useAnalyses, useEnvs, useProjects, useSubmit } from "../api/queries";
import { JOB_STATES, SEVERITIES, formatTime, utcTooltip } from "../lib/format";

const LAST_KEY = "assistant.lastScope";
const route = useRoute();
const router = useRouter();

function remembered(): { project?: string; env?: string } {
  try {
    return JSON.parse(localStorage.getItem(LAST_KEY) ?? "{}") as { project?: string; env?: string };
  } catch {
    return {};
  }
}

const project = ref<string | null>((route.query.project as string) || null);
const env = ref<string | null>((route.query.env as string) || null);
const projects = useProjects();
const envs = useEnvs(project);
const analyses = useAnalyses(project, env);
const submit = useSubmit();

// Reapply the last pair only if it is still discovered.
watch(
  () => projects.data.value,
  (data) => {
    if (!data || project.value) return;
    const last = remembered();
    if (last.project && data.items.some((p) => p.project === last.project)) {
      project.value = last.project;
      env.value = last.env ?? null;
    }
  },
  { immediate: true },
);

// Changing the project always clears the environment and stale results.
function selectProject(value: string) {
  project.value = value || null;
  env.value = null;
  submit.reset();
}

watch(
  () => envs.data.value,
  (data) => {
    if (!data || data.project !== project.value) return; // ignore responses for a previous project
    if (env.value && !data.items.some((e) => e.env === env.value)) env.value = null;
    if (!env.value && data.items.length === 1) env.value = data.items[0]?.env ?? null;
  },
);

const envOptions = computed(() =>
  envs.data.value && envs.data.value.project === project.value ? envs.data.value.items : [],
);
const canAnalyze = computed(() => !!project.value && !!env.value && !submit.isPending.value);

async function analyze() {
  if (!project.value || !env.value) return;
  try {
    localStorage.setItem(LAST_KEY, JSON.stringify({ project: project.value, env: env.value }));
  } catch {
    /* ignore */
  }
  let result;
  try {
    result = await submit.mutateAsync({ project: project.value, env: env.value });
  } catch {
    return; // shown from submit.error
  }
  await router.push({
    path: `/analyses/${result.analysis.analysis_id}`,
    query: result.duplicate_of_active ? { duplicate: "1" } : {},
  });
}

const submitError = computed(() => (submit.error.value instanceof ApiError ? submit.error.value : null));
const reportLink = (id: string, available: boolean) => (available ? `/reports/${id}` : `/analyses/${id}`);
</script>

<template>
  <section aria-labelledby="start-title" class="stack">
    <h1 id="start-title">Analyse a project environment</h1>
    <p class="muted">Anomalies in the latest 24 hours, compared with up to 14 preceding days, plus a 14-day trend.</p>

    <div v-if="projects.isPending.value" aria-busy="true"><div class="skeleton" style="width: 40%" /></div>
    <div v-else-if="projects.isError.value" role="alert" class="banner error">
      <strong>Metrics source unreachable.</strong>
      {{ projects.error.value?.message }}
      <button type="button" @click="projects.refetch()">Retry</button>
      <p class="muted">Saved reports can still be opened from their links.</p>
    </div>
    <div v-else-if="projects.data.value && projects.data.value.items.length === 0" class="banner">
      No series with a <code>project</code> label were found in the last 28 days.
    </div>

    <form v-else class="row selectors" @submit.prevent="analyze">
      <label>
        Project
        <select :value="project ?? ''" @change="selectProject(($event.target as HTMLSelectElement).value)">
          <option value="" disabled>Select a project</option>
          <option v-for="p in projects.data.value?.items" :key="p.project" :value="p.project">{{ p.project }}</option>
        </select>
      </label>
      <label>
        Environment
        <select v-model="env" :disabled="!project || envs.isPending.value" :aria-busy="envs.isFetching.value">
          <option :value="null" disabled>{{ project ? (envs.isPending.value ? "Loading…" : "Select an environment") : "Select a project first" }}</option>
          <option v-for="e in envOptions" :key="e.env" :value="e.env">{{ e.env }}</option>
        </select>
      </label>
      <button type="submit" class="primary" :disabled="!canAnalyze">
        {{ submit.isPending.value ? "Starting…" : "Analyze" }}
      </button>
    </form>

    <p v-if="envs.isError.value" role="alert" class="banner error">Environments could not be loaded: {{ envs.error.value?.message }}</p>
    <p v-if="submitError" role="alert" class="banner error">
      <template v-if="submitError.problem.code === 'queue_full'">
        The analysis queue is full. Try again in {{ submitError.retryAfter ?? 30 }} seconds.
      </template>
      <template v-else>{{ submitError.problem.title }}<span v-if="submitError.problem.detail">: {{ submitError.problem.detail }}</span></template>
    </p>

    <template v-if="project && env">
      <h2>Recent reports for {{ project }} / {{ env }}</h2>
      <div v-if="analyses.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>
      <p v-else-if="analyses.data.value?.items.length === 0" class="muted">No analyses yet for this scope.</p>
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
                <span v-if="job.finding_counts">
                  <template v-for="s in SEVERITIES" :key="s">
                    <span v-if="job.finding_counts[s]" :class="`sev-text-${s}`">{{ job.finding_counts[s] }} {{ s }} </span>
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
.sev-text-critical { color: var(--sev-critical); }
.sev-text-high { color: var(--sev-high); }
.sev-text-medium { color: var(--sev-medium); }
.sev-text-low { color: var(--sev-low); }
</style>
