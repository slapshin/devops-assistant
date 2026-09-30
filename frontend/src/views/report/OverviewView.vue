<script setup lang="ts">
import { computed } from "vue";
import CoverageTable from "../../components/CoverageTable.vue";
import ExplanationPanel from "../../components/ExplanationPanel.vue";
import FindingCard from "../../components/FindingCard.vue";
import { SEVERITIES } from "../../lib/format";
import { useReportContext } from "./context";

const { report } = useReportContext();
const link = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
const counts = computed(() => SEVERITIES.map((s) => [s, report.value.findings.filter((f) => f.severity === s).length] as const));
const evaluated = computed(() => report.value.coverage.filter((c) => c.status === "anomalous" || c.status === "no_anomaly").length);
const unsupported = computed(() => report.value.coverage.filter((c) => c.status === "unsupported").length);
const insufficient = computed(() => report.value.coverage.filter((c) => c.status === "insufficient_data" || c.status === "source_error").length);
const top = computed(() => report.value.findings.slice(0, 5));
const summary = computed(() => report.value.trend_summary);
</script>

<template>
  <div class="overview">
    <section class="strip card" aria-label="Summary">
      <div class="row">
        <span v-for="[s, n] in counts" :key="s" :class="`count sev-${s}`"><strong>{{ n }}</strong> {{ s }}</span>
      </div>
      <p v-if="report.findings[0]">
        Most severe: <RouterLink :to="link(report.findings[0].finding_id)">{{ report.findings[0].title }} — {{ report.findings[0].entity.display_name }}</RouterLink>
      </p>
      <p class="muted">
        {{ evaluated }} of {{ report.coverage.length }} signal families evaluated · {{ unsupported }} unsupported · {{ insufficient }} insufficient data or source errors
      </p>
      <p v-if="summary" class="muted">
        14-day trend: <strong>{{ summary.direction }}</strong> ({{ summary.confidence }} confidence) — {{ summary.reason }}
      </p>
    </section>

    <div class="columns">
      <section aria-labelledby="top-title" class="stack">
        <h2 id="top-title">Top findings (latest 24 h)</h2>
        <template v-if="top.length">
          <FindingCard v-for="f in top" :key="f.finding_id" :finding="f" :to="link(f.finding_id)" />
          <RouterLink :to="`/reports/${report.analysis_id}/findings`">View all {{ report.findings.length }} findings</RouterLink>
        </template>
        <p v-else-if="evaluated > 0" class="card">No anomalies detected in evaluated signals. See coverage below for what was not evaluated.</p>
        <p v-else class="card" role="status">No signals could be evaluated, so this report makes no health claim.</p>
      </section>
      <ExplanationPanel :report="report" :finding-link="link" />
    </div>

    <CoverageTable :report="report" />
  </div>
</template>

<style scoped>
.count { margin-right: calc(var(--space) * 2); }
.sev-critical { color: var(--sev-critical); }
.sev-high { color: var(--sev-high); }
.sev-medium { color: var(--sev-medium); }
.sev-low { color: var(--sev-low); }
.columns { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: calc(var(--space) * 2); align-items: start; }
@media (max-width: 899px) { .columns { grid-template-columns: 1fr; } }
</style>
