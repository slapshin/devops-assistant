<script setup lang="ts">
import { computed } from "vue";
import CoverageGrid from "../../components/CoverageGrid.vue";
import CoverageTable from "../../components/CoverageTable.vue";
import ExplanationPanel from "../../components/ExplanationPanel.vue";
import FindingCard from "../../components/FindingCard.vue";
import IncidentTimeline from "../../components/IncidentTimeline.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { headline, ratioText } from "../../lib/findings";
import { FAMILY_LABELS, SEVERITIES, capitalize } from "../../lib/format";
import { useReportContext } from "./context";

const TOP_FINDINGS_COUNT = 5;
/** Families that were not evaluated: problems there would not show up in this report. */
const BLIND_SPOT_REASONS = {
  unsupported: "no metrics found",
  insufficient_data: "not enough history",
  source_error: "queries failed",
} as const;

const { report, evidenceById } = useReportContext();

const severityCounts = computed(() =>
  SEVERITIES.map((s) => [s, report.value.findings.filter((f) => f.severity === s).length] as const).filter(([, n]) => n > 0),
);
const evaluatedCount = computed(() => report.value.coverage.filter((c) => c.status === "anomalous" || c.status === "no_anomaly").length);
const topFindings = computed(() => report.value.findings.slice(0, TOP_FINDINGS_COUNT));
const worst = computed(() => report.value.findings[0] ?? null);
const lede = computed(() => {
  const f = worst.value;
  if (!f) return "";

  const all = report.value.findings;
  const newCount = all.filter((x) => x.recurrence === "new").length;
  const parts = [ratioText(f)];
  if (all.length > 1 || newCount === 0) parts.push(recurrenceSummary(newCount, all.length - newCount));
  return capitalize(parts.filter(Boolean).join(" "));
});
const blindSpots = computed(() =>
  report.value.coverage
    .filter((c) => c.status in BLIND_SPOT_REASONS)
    .map((c) => ({ family: FAMILY_LABELS[c.family] ?? c.family, why: BLIND_SPOT_REASONS[c.status as keyof typeof BLIND_SPOT_REASONS] })),
);

const link = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
const evidenceFor = (ids: string[]) => (ids[0] ? evidenceById.value.get(ids[0]) ?? null : null);

/** "2 findings are new today, 1 happened before." */
function recurrenceSummary(newCount: number, beforeCount: number): string {
  const newPart = newCount ? `${newCount} ${newCount === 1 ? "finding is" : "findings are"} new today` : "";
  const beforePart = beforeCount ? `${beforeCount} happened before` : "";
  return [newPart, beforePart].filter(Boolean).join(", ") + ".";
}

/** Separator after item `index` of an English list: "a, b and c". */
function listSeparator(index: number, length: number): string {
  if (index < length - 2) return ", ";
  if (index === length - 2) return " and ";
  return "";
}
</script>

<template>
  <div class="overview">
    <section class="hero" aria-label="Summary">
      <div class="verdict">
        <template v-if="worst">
          <div class="row">
            <span v-for="[s, n] in severityCounts" :key="s" class="count"><SeverityChip :severity="s" /><span>× {{ n }}</span></span>
          </div>
          <h1>{{ headline(worst) }}</h1>
          <p class="lede">{{ lede }}</p>
          <p class="most">
            Most severe: <RouterLink :to="link(worst.finding_id)">{{ worst.title }} — {{ worst.entity.display_name }}</RouterLink>
          </p>
        </template>
        <h1 v-else-if="evaluatedCount > 0">No anomalies detected in evaluated signals</h1>
        <h1 v-else role="status">No signals could be evaluated, so this report makes no health claim</h1>
        <p v-if="report.trend_summary" class="muted trend">
          14-day trend: <RouterLink :to="`/reports/${report.analysis_id}/trends`">{{ report.trend_summary.direction }}</RouterLink>
          ({{ report.trend_summary.confidence }} confidence) — {{ report.trend_summary.reason }}
        </p>
      </div>
      <div class="card blind">
        <span class="label-caps">Blind spots</span>
        <p v-if="blindSpots.length">
          <template v-for="(b, i) in blindSpots" :key="b.family">
            <strong>{{ b.family }}</strong> ({{ b.why }}){{ listSeparator(i, blindSpots.length) }}
          </template>
          {{ blindSpots.length === 1 ? "is" : "are" }} not evaluated. Problems there would not show up in this report.
        </p>
        <p v-else>Every signal family was evaluated.</p>
        <p class="muted small">
          {{ evaluatedCount }} of {{ report.coverage.length }} signal families evaluated · <a href="#coverage">Coverage</a>
        </p>
      </div>
    </section>

    <IncidentTimeline
      v-if="report.findings.length"
      :findings="report.findings"
      :start="report.windows.latest_day.start"
      :end="report.windows.latest_day.end"
      :link="link"
    />

    <section v-if="topFindings.length" aria-labelledby="top-title" class="top">
      <div class="top-head">
        <h2 id="top-title">Top findings (latest 24 h)</h2>
        <RouterLink :to="`/reports/${report.analysis_id}/findings`">View all {{ report.findings.length }} findings</RouterLink>
      </div>
      <div class="cards">
        <FindingCard v-for="f in topFindings" :key="f.finding_id" :finding="f" :to="link(f.finding_id)" :evidence="evidenceFor(f.evidence_ids)" />
      </div>
    </section>

    <div class="columns">
      <ExplanationPanel :report="report" :finding-link="link" />
      <CoverageGrid :coverage="report.coverage" />
    </div>

    <CoverageTable :report="report" class="card" />
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: calc(var(--space) * 3); }
.hero { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr)); gap: calc(var(--space) * 3); align-items: stretch; }
.verdict { display: flex; flex-direction: column; gap: 10px; }
.verdict h1 { margin: 0; font-size: 2rem; line-height: 1.15; letter-spacing: -0.01em; text-wrap: pretty; }
.verdict p { margin: 0; }
.count { display: inline-flex; align-items: center; gap: 4px; font-size: 0.9rem; }
.lede { font-size: 1.05rem; color: var(--text-2); max-width: 62ch; }
.most, .trend { font-size: 0.9rem; }
.blind { display: flex; flex-direction: column; gap: 10px; justify-content: center; }
.blind p { margin: 0; }
.small { font-size: 0.85rem; }
.top { display: flex; flex-direction: column; gap: 12px; }
.top-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.top-head h2 { margin: 0; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: calc(var(--space) * 2); }
.columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(460px, 100%), 1fr)); gap: calc(var(--space) * 2); align-items: start; }
</style>
