<script setup lang="ts">
import { computed } from "vue";
import type { ProjectSummary } from "../../api/client";
import { JOB_STATES, formatAge, utcTooltip } from "../../lib/format";
import { scheduleSummary, sourceSummary } from "../../lib/projects";
import SeverityCounts from "../SeverityCounts.vue";
import HealthBadge from "./HealthBadge.vue";
import MatcherChips from "./MatcherChips.vue";
import RunAnalysis from "./RunAnalysis.vue";
import TrendSparkline from "./TrendSparkline.vue";

const props = defineProps<{ project: ProjectSummary }>();

const latest = computed(() => props.project.latest_analysis ?? null);
const latestAt = computed(() => latest.value?.finished_at ?? latest.value?.created_at ?? null);
const latestLink = computed(() => {
  const job = latest.value;
  if (!job) return null;
  return job.report_available ? `/reports/${job.analysis_id}` : `/analyses/${job.analysis_id}`;
});
</script>

<template>
  <article class="card project" :aria-labelledby="`project-${project.project_id}`">
    <div class="identity">
      <h2 :id="`project-${project.project_id}`">
        <RouterLink :to="`/projects/${project.project_id}`">{{ project.name }}</RouterLink>
      </h2>
      <MatcherChips v-if="project.matchers.length" :matchers="project.matchers" />
      <p class="source">
        <span v-for="s in project.sources" :key="s.kind" class="mono muted">{{ sourceSummary(s) }}</span>
        <span v-if="!project.sources.length" class="mono muted">No data source</span>
        <HealthBadge :project="project" />
      </p>
      <p v-if="project.schedule" class="muted schedule">Scheduled: {{ scheduleSummary(project.schedule) }}</p>
    </div>

    <div class="latest">
      <span class="lbl">Latest report</span>
      <template v-if="latest && latestAt && latestLink">
        <RouterLink :to="latestLink" :title="utcTooltip(latestAt)">
          {{ JOB_STATES[latest.state] ?? latest.state }} · {{ formatAge(latestAt) }}
        </RouterLink>
        <SeverityCounts v-if="latest.report_available" :counts="latest.finding_counts" />
        <span v-else-if="latest.error" class="muted">{{ latest.error.code.replaceAll("_", " ") }}</span>
      </template>
      <span v-else class="muted">No report yet</span>
    </div>

    <div class="trend">
      <span class="lbl">14-day anomalies</span>
      <TrendSparkline v-if="latest?.daily_episodes" :days="latest.daily_episodes" />
      <span v-else class="muted">No trend yet</span>
    </div>

    <RunAnalysis class="actions" :project="project" />
  </article>
</template>

<style scoped>
.project {
  display: grid;
  grid-template-columns: minmax(0, 2.2fr) minmax(0, 1.4fr) minmax(0, 1fr) 15rem;
  gap: calc(var(--space) * 2);
  align-items: start;
}
h2 { margin: 0 0 6px; font-size: 15px; }
.identity > * + * { margin-top: 6px; }
.schedule { margin: 0; font-size: 12px; }
.source { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; margin-bottom: 0; }
.latest, .trend { display: flex; flex-direction: column; gap: 4px; }
@media (max-width: 960px) {
  .project { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .actions { grid-column: 1 / -1; }
}
@media (max-width: 600px) {
  .project { grid-template-columns: minmax(0, 1fr); }
}
</style>
