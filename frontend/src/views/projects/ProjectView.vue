<script setup lang="ts">
import { computed } from "vue";
import { ApiError } from "../../api/client";
import { useAnalyses, useProject } from "../../api/queries";
import AnalysesTable from "../../components/AnalysesTable.vue";
import MatcherChips from "../../components/projects/MatcherChips.vue";
import RunAnalysis from "../../components/projects/RunAnalysis.vue";
import SourceHealth from "../../components/projects/SourceHealth.vue";
import SourceIcon from "../../components/projects/SourceIcon.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import { formatTime, utcTooltip } from "../../lib/format";
import { WAZUH_INDEX_PATTERN, cloudflareSource, prometheusSource, scheduleSummary, sentrySource, sourceHost, wazuhSource } from "../../lib/projects";

const HISTORY_PAGE_SIZE = 20;
const AUTH_LABELS: Record<string, string> = { none: "No authentication", bearer: "Bearer token", basic: "Basic auth" };

const props = defineProps<{ projectId: string }>();

const project = useProject(() => props.projectId);
const history = useAnalyses(() => props.projectId, HISTORY_PAGE_SIZE);

const notFound = computed(() => project.error.value instanceof ApiError && project.error.value.problem.status === 404);
const prometheus = computed(() => prometheusSource(project.data.value));
const cloudflare = computed(() => cloudflareSource(project.data.value));
const sentry = computed(() => sentrySource(project.data.value));
const wazuh = computed(() => wazuhSource(project.data.value));
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
        <div class="sources" role="group" aria-label="Sources">
          <dt class="source-kind"><SourceIcon kind="prometheus" />Prometheus</dt>
          <dd v-if="prometheus">
            <SourceHealth :project="project.data.value" kind="prometheus" />
            <span class="mono">{{ sourceHost(prometheus.url) }}</span>
            · {{ AUTH_LABELS[prometheus.auth.type] }}<template v-if="!prometheus.tls_verify"> · TLS not verified</template>
            <MatcherChips class="chips" :matchers="project.data.value.matchers" />
          </dd>
          <dd v-else class="muted">Not configured</dd>
          <dt class="source-kind"><SourceIcon kind="cloudflare" />Cloudflare</dt>
          <dd v-if="cloudflare">
            <SourceHealth :project="project.data.value" kind="cloudflare" />
            zone <span class="mono">{{ cloudflare.zone_id }}</span>
            · {{ cloudflare.hostnames.length ? cloudflare.hostnames.join(", ") : "all hostnames" }}
            <template v-if="cloudflare.api_url.startsWith('synthetic:')"> · <span class="mono">{{ sourceHost(cloudflare.api_url) }}</span></template>
          </dd>
          <dd v-else class="muted">Not configured</dd>
          <dt class="source-kind"><SourceIcon kind="sentry" />Sentry</dt>
          <dd v-if="sentry">
            <SourceHealth :project="project.data.value" kind="sentry" />
            <span class="mono">{{ sentry.organization }}: {{ sentry.projects.join(", ") }}</span>
            · {{ sentry.environment ?? "all environments" }}
            <template v-if="sentry.api_url !== 'https://sentry.io'"> · <span class="mono">{{ sourceHost(sentry.api_url) }}</span></template>
            <template v-if="sentry.tls_verify === false"> · TLS not verified</template>
            <MatcherChips
              v-if="sentry.tags?.length"
              class="chips"
              label="Sentry tag filters"
              :matchers="sentry.tags.map((t) => ({ name: t.key, value: t.value }))"
            />
          </dd>
          <dd v-else class="muted">Not configured</dd>
          <dt class="source-kind"><SourceIcon kind="wazuh" />Wazuh</dt>
          <dd v-if="wazuh">
            <SourceHealth :project="project.data.value" kind="wazuh" />
            <span class="mono">{{ sourceHost(wazuh.api_url) }}</span>
            · {{ [...(wazuh.agents ?? []), ...(wazuh.groups ?? []).map((g) => `group ${g}`)].join(", ") || "agents by label" }}
            <template v-if="wazuh.index_pattern !== WAZUH_INDEX_PATTERN"> · <span class="mono">{{ wazuh.index_pattern }}</span></template>
            <template v-if="wazuh.tls_verify === false"> · TLS not verified</template>
            <MatcherChips
              v-if="wazuh.labels?.length"
              class="chips"
              label="Wazuh agent labels"
              :matchers="wazuh.labels.map((l) => ({ name: l.key, value: l.value }))"
            />
          </dd>
          <dd v-else class="muted">Not configured</dd>
        </div>
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
.sources {
  grid-column: 1 / -1; display: grid; grid-template-columns: subgrid; gap: inherit;
  padding: var(--space); margin-bottom: var(--space);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
}
.sources::before { content: "Sources"; grid-column: 1 / -1; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.source-kind { display: inline-flex; align-items: center; gap: 6px; align-self: start; }
.chips { margin-top: 4px; }
@media (max-width: 600px) {
  .facts { grid-template-columns: minmax(0, 1fr); }
  .facts dd { margin-bottom: var(--space); }
}
</style>
