<script setup lang="ts">
import { computed } from "vue";
import { ApiError } from "../../api/client";
import { useAnalyses, useProject } from "../../api/queries";
import AnalysesTable from "../../components/AnalysesTable.vue";
import HealthBadge from "../../components/projects/HealthBadge.vue";
import MatcherChips from "../../components/projects/MatcherChips.vue";
import RunAnalysis from "../../components/projects/RunAnalysis.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import { formatTime, utcTooltip } from "../../lib/format";
import { scheduleSummary, sourceHost } from "../../lib/projects";

const HISTORY_PAGE_SIZE = 20;
const AUTH_LABELS: Record<string, string> = { none: "No authentication", bearer: "Bearer token", basic: "Basic auth" };

const props = defineProps<{ projectId: string }>();

const project = useProject(() => props.projectId);
const history = useAnalyses(() => props.projectId, HISTORY_PAGE_SIZE);

const notFound = computed(() => project.error.value instanceof ApiError && project.error.value.problem.status === 404);
const source = computed(() => project.data.value?.sources[0] ?? null);
const jobs = computed(() => history.data.value?.pages.flatMap((p) => p.items) ?? []);
</script>

<template>
  <AppTopbar :crumbs="[{ label: 'Projects', to: '/' }, { label: project.data.value?.name ?? 'Project' }]" />
  <main class="page" aria-labelledby="project-title">
    <div v-if="project.isPending.value" aria-busy="true"><div class="skeleton" style="width: 40%" /><div class="skeleton" /></div>
    <div v-else-if="notFound" role="alert" class="banner error">This project does not exist (it may have been deleted).</div>
    <div v-else-if="project.isError.value" role="alert" class="banner error">
      {{ project.error.value?.message }} <button type="button" @click="project.refetch()">Retry</button>
    </div>

    <template v-else-if="project.data.value">
      <div class="page-head">
        <h1 id="project-title">{{ project.data.value.name }}</h1>
        <div class="row">
          <RouterLink :to="`/projects/${projectId}/edit`" class="button">Edit</RouterLink>
          <RouterLink :to="{ path: '/projects/new', query: { from: projectId } }" class="button">Clone</RouterLink>
          <RunAnalysis :project="project.data.value" />
        </div>
      </div>
      <p v-if="project.data.value.description">{{ project.data.value.description }}</p>

      <dl class="facts card">
        <dt>Labels</dt>
        <dd><MatcherChips :matchers="project.data.value.matchers" /></dd>
        <dt>Metrics source</dt>
        <dd v-if="source">
          <span class="mono">{{ sourceHost(source.url) }}</span>
          · {{ AUTH_LABELS[source.auth.type] }}<template v-if="!source.tls_verify"> · TLS not verified</template>
          · <HealthBadge :project="project.data.value" />
        </dd>
        <dd v-else class="muted">Not configured</dd>
        <dt>Schedule</dt>
        <dd v-if="project.data.value.schedule">
          {{ scheduleSummary(project.data.value.schedule) }}
          <template v-if="project.data.value.next_scheduled_run">
            · next <span :title="utcTooltip(project.data.value.next_scheduled_run)">{{ formatTime(project.data.value.next_scheduled_run) }}</span>
          </template>
        </dd>
        <dd v-else class="muted">On demand only</dd>
        <dt>Saved reports</dt>
        <dd>
          {{ project.data.value.report_count }}
          <span v-if="project.data.value.keep_reports != null" class="muted">· latest {{ project.data.value.keep_reports }} kept</span>
        </dd>
      </dl>

      <h2 class="section-title">Analyses</h2>
      <div v-if="history.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>
      <p v-else-if="history.isError.value" role="alert" class="banner error">History could not be loaded: {{ history.error.value?.message }}</p>
      <p v-else-if="jobs.length === 0" class="muted">No analyses yet.</p>
      <template v-else>
        <AnalysesTable :jobs="jobs" />
        <button v-if="history.hasNextPage.value" type="button" :disabled="history.isFetchingNextPage.value" @click="history.fetchNextPage()">
          {{ history.isFetchingNextPage.value ? "Loading…" : "Load older analyses" }}
        </button>
      </template>
    </template>
  </main>
</template>

<style scoped>
.section-title { margin: 8px 0 0; }
.facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px calc(var(--space) * 2); margin: 0; }
.facts dt { font-weight: 600; color: var(--muted); }
.facts dd { margin: 0; }
@media (max-width: 600px) {
  .facts { grid-template-columns: minmax(0, 1fr); }
  .facts dd { margin-bottom: var(--space); }
}
</style>
