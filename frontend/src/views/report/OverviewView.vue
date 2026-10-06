<script setup lang="ts">
import { computed, ref } from "vue";
import type { Finding } from "../../api/client";
import CoverageGrid from "../../components/CoverageGrid.vue";
import CoverageTable from "../../components/CoverageTable.vue";
import ExplanationPanel from "../../components/ExplanationPanel.vue";
import FindingCard from "../../components/FindingCard.vue";
import IncidentTimeline from "../../components/IncidentTimeline.vue";
import { headline, longestBaselineDays, ratioText } from "../../lib/findings";
import { FAMILY_LABELS, SEVERITIES, capitalize } from "../../lib/format";
import { useReportContext } from "./context";

/** Two full rows of three finding panels. */
const TOP_FINDINGS_COUNT = 6;
/** Families that were not evaluated: problems there would not show up in this report. */
const BLIND_SPOT_REASONS = {
  unsupported: "no metrics found",
  insufficient_data: "not enough history",
  source_error: "queries failed",
} as const;

/** Critical, high and medium always get a stat panel; low only when present. */
const ALWAYS_SHOWN_SEVERITIES = new Set(["critical", "high", "medium"]);

const { report, evidenceById } = useReportContext();

const episodesOpen = ref(true);
const coverageOpen = ref(true);

const severityStats = computed(() =>
  SEVERITIES.map((severity) => {
    const findings = report.value.findings.filter((f) => f.severity === severity);
    return { severity, count: findings.length, detail: severityDetail(findings) };
  }).filter((s) => s.count > 0 || ALWAYS_SHOWN_SEVERITIES.has(s.severity)),
);
const newCount = computed(() => report.value.findings.filter((f) => f.recurrence === "new").length);
const baselineDays = computed(() => longestBaselineDays(report.value.coverage));
const evaluatedCount = computed(() => report.value.coverage.filter((c) => c.status === "anomalous" || c.status === "no_anomaly").length);
const topFindings = computed(() => report.value.findings.slice(0, TOP_FINDINGS_COUNT));
const worst = computed(() => report.value.findings[0] ?? null);
const lede = computed(() => {
  const f = worst.value;
  if (!f) return "";

  const all = report.value.findings;
  const parts = [ratioText(f)];
  if (all.length > 1 || newCount.value === 0) parts.push(recurrenceSummary(newCount.value, all.length - newCount.value));
  return capitalize(parts.filter(Boolean).join(" "));
});
const blindSpots = computed(() =>
  report.value.coverage
    .filter((c) => c.status in BLIND_SPOT_REASONS)
    .map((c) => ({ family: FAMILY_LABELS[c.family] ?? c.family, why: BLIND_SPOT_REASONS[c.status as keyof typeof BLIND_SPOT_REASONS] })),
);

const link = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
const evidenceFor = (ids: string[]) => (ids[0] ? evidenceById.value.get(ids[0]) ?? null : null);

/** "CPU · new" for a single finding, "2 new · 1 seen before" for several. */
function severityDetail(findings: Finding[]): string {
  const [only] = findings;
  if (!only) return "none";
  if (findings.length === 1) return `${FAMILY_LABELS[only.family] ?? only.family} · ${only.recurrence === "new" ? "new" : "seen before"}`;

  const fresh = findings.filter((f) => f.recurrence === "new").length;
  return [fresh ? `${fresh} new` : "", findings.length - fresh ? `${findings.length - fresh} seen before` : ""].filter(Boolean).join(" · ");
}

/** "2 findings are new today, 1 happened before." */
function recurrenceSummary(freshCount: number, beforeCount: number): string {
  const newPart = freshCount ? `${freshCount} ${freshCount === 1 ? "finding is" : "findings are"} new today` : "";
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
  <div class="dash">
    <section
      v-for="s in severityStats"
      :key="s.severity"
      class="panel s4"
      :class="{ [`sb-${s.severity}`]: s.count > 0 }"
      :aria-label="`${capitalize(s.severity)} findings`"
    >
      <div class="ph"><h2>{{ capitalize(s.severity) }}</h2></div>
      <div class="stat"><div class="v">{{ s.count }}</div><div class="d">{{ s.detail }}</div></div>
    </section>
    <section class="panel s4" aria-label="New today">
      <div class="ph"><h2>New today</h2></div>
      <div class="stat"><div class="v">{{ newCount }}</div><div class="d">of {{ report.findings.length }} {{ report.findings.length === 1 ? "finding" : "findings" }}</div></div>
    </section>
    <section class="panel s4" aria-label="Signal coverage">
      <div class="ph"><h2>Signal coverage</h2></div>
      <div class="stat">
        <div class="v" :class="evaluatedCount === report.coverage.length ? 'ok' : 'warn'">
          {{ evaluatedCount }}<span class="of"> / {{ report.coverage.length }}</span>
        </div>
        <div class="cov-bar" aria-hidden="true">
          <span class="ok-part" :style="{ flexGrow: evaluatedCount }" />
          <span class="gap-part" :style="{ flexGrow: report.coverage.length - evaluatedCount }" />
        </div>
      </div>
    </section>
    <section class="panel s4" aria-label="Blind spots">
      <div class="ph"><h2>Blind spots</h2></div>
      <div class="stat">
        <div class="v" :class="blindSpots.length ? 'warn' : 'ok'">{{ blindSpots.length }}</div>
        <div class="d">{{ blindSpots.length ? blindSpots.map((b) => b.family).join(" · ") : "every family evaluated" }}</div>
      </div>
    </section>

    <section class="panel s24" aria-labelledby="summary-title">
      <div class="ph"><h2 id="summary-title">Summary</h2><span class="sub">· latest 24 h</span></div>
      <div class="pb summary">
        <template v-if="worst">
          <h1>{{ headline(worst) }}</h1>
          <p class="lede">{{ lede }}</p>
          <p class="most">
            Most severe: <RouterLink :to="link(worst.finding_id)">{{ worst.title }} — {{ worst.entity.display_name }}</RouterLink>
          </p>
        </template>
        <h1 v-else-if="evaluatedCount > 0">No anomalies detected in evaluated signals</h1>
        <h1 v-else role="status">No signals could be evaluated, so this report makes no health claim</h1>
        <p v-if="report.trend_summary" class="lbl">
          14-day trend: <RouterLink :to="`/reports/${report.analysis_id}/trends`">{{ report.trend_summary.direction }}</RouterLink>
          ({{ report.trend_summary.confidence }} confidence) — {{ report.trend_summary.reason }}
        </p>
        <div class="blind" :class="{ none: !blindSpots.length }">
          <svg v-if="blindSpots.length" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true" class="warn-icon"><path d="M8 1.5 15 14H1z" stroke-linejoin="round" /><path d="M8 6v4M8 11.5v.5" /></svg>
          <p v-if="blindSpots.length">
            <template v-for="(b, i) in blindSpots" :key="b.family">
              <strong>{{ b.family }}</strong> ({{ b.why }}){{ listSeparator(i, blindSpots.length) }}
            </template>
            {{ blindSpots.length === 1 ? "is" : "are" }} not evaluated. Problems there would not show up in this report.
          </p>
          <p v-else>Every signal family was evaluated.</p>
        </div>
      </div>
    </section>
    <ExplanationPanel class="s24" :report="report" :finding-link="link" />

    <template v-if="report.findings.length">
      <h2 class="row-h">
        <button type="button" :aria-expanded="episodesOpen" aria-controls="episodes-row" @click="episodesOpen = !episodesOpen">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" /></svg>
          Episodes
        </button>
        <span class="sub">latest 24 h · {{ topFindings.length < report.findings.length ? `top ${topFindings.length} of ${report.findings.length}` : report.findings.length }} findings</span>
        <RouterLink :to="`/reports/${report.analysis_id}/findings`" class="all">View all {{ report.findings.length }} findings</RouterLink>
      </h2>
      <div v-show="episodesOpen" id="episodes-row" class="dash-sub s24">
        <IncidentTimeline
          class="s24"
          :findings="report.findings"
          :start="report.windows.latest_day.start"
          :end="report.windows.latest_day.end"
          :link="link"
        />
        <FindingCard v-for="f in topFindings" :key="f.finding_id" class="s8" :finding="f" :to="link(f.finding_id)" :evidence="evidenceFor(f.evidence_ids)" />
      </div>
    </template>

    <h2 id="coverage" class="row-h">
      <button type="button" :aria-expanded="coverageOpen" aria-controls="coverage-row" @click="coverageOpen = !coverageOpen">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" /></svg>
        Coverage
      </button>
      <span class="sub">{{ evaluatedCount }} of {{ report.coverage.length }} signal families evaluated</span>
    </h2>
    <div v-show="coverageOpen" id="coverage-row" class="dash-sub s24">
      <CoverageGrid class="s24" :coverage="report.coverage" :baseline-days="baselineDays" />
      <CoverageTable class="s24" :report="report" />
    </div>
  </div>
</template>

<style scoped>
.v.ok { color: var(--ok); }
.v.warn { color: var(--med); }
.of { font-size: 22px; color: var(--muted); }
.cov-bar { display: flex; gap: 2px; margin-top: 8px; }
.cov-bar span { height: 6px; flex-basis: 0; border-radius: 1px; }
.ok-part { background: var(--f-ok); }
.gap-part { background: var(--f-med); }
.summary { gap: 12px; padding: 8px 20px 18px; }
.summary h1 { margin: 0; text-wrap: pretty; }
.lede { font-size: 15px; max-width: 70ch; text-wrap: pretty; }
.most { font-size: 13px; }
.blind { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--canvas); font-size: 13px; }
.blind strong { color: var(--strong); }
.blind.none { color: var(--muted); }
.warn-icon { flex-shrink: 0; margin-top: 2px; color: var(--med); }
.all { margin-left: auto; font-size: 13px; font-weight: 400; }
</style>
