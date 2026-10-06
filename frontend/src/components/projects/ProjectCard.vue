<script setup lang="ts">
import { computed } from "vue";
import type { ProjectSummary, SourceKind } from "../../api/client";
import { JOB_STATES, formatAge, utcTooltip } from "../../lib/format";
import { SOURCE_KIND_LABELS, scheduleSummary } from "../../lib/projects";
import SeverityCounts from "../SeverityCounts.vue";
import RunAnalysis from "./RunAnalysis.vue";
import SourceIcon from "./SourceIcon.vue";
import TrendSparkline from "./TrendSparkline.vue";

const props = defineProps<{ project: ProjectSummary }>();

/** Every source kind, dimmed unless the project configures it. */
const sources = computed(() =>
  (Object.keys(SOURCE_KIND_LABELS) as SourceKind[]).map((kind) => {
    const configured = props.project.sources.some((s) => s.kind === kind);
    return { kind, label: `${SOURCE_KIND_LABELS[kind]}: ${configured ? "configured" : "not configured"}`, configured };
  }),
);
const retention = computed(() =>
  props.project.keep_reports != null ? `Keeps latest ${props.project.keep_reports} reports` : "Keeps all reports",
);

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
      <ul class="sources" aria-label="Data sources">
        <li
          v-for="s in sources"
          :key="s.kind"
          :class="{ off: !s.configured }"
          role="img"
          :aria-label="s.label"
          :title="s.label"
        >
          <SourceIcon :kind="s.kind" />
        </li>
      </ul>
      <p v-if="project.schedule" class="muted meta">Scheduled: {{ scheduleSummary(project.schedule) }}</p>
      <p class="muted meta">{{ retention }}</p>
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
.meta { margin: 0; font-size: 12px; }
.sources { list-style: none; margin: 0; padding: 0; display: flex; gap: 8px; }
.sources li { display: inline-flex; cursor: help; }
.sources .off { color: var(--muted); opacity: 0.35; filter: grayscale(1); }
.latest, .trend { display: flex; flex-direction: column; gap: 4px; }
@media (max-width: 960px) {
  .project { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .actions { grid-column: 1 / -1; }
}
@media (max-width: 600px) {
  .project { grid-template-columns: minmax(0, 1fr); }
}
</style>
