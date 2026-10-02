<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { DailyTrend } from "../../api/client";
import DataTable from "../../components/DataTable.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { MEASURES, dayLabel, type Measure } from "../../lib/charts";
import { FAMILY_LABELS, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";

const MS_PER_MINUTE = 60_000;
/** An entity/signal pair counts as recurring once it has episodes on this many days. */
const MIN_RECURRING_DAYS = 3;
/** Below this, a day's coverage strip is shown in amber. */
const FULL_COVERAGE_RATIO = 0.95;
/** The day bars are the day buttons: height is the measure relative to the busiest day. */
const BAR_MAX_PX = 160;
const BAR_MIN_PX = 4;
const BAR_ZERO_PX = 2;
const NO_VALUE = "—";

const STATUS_TEXT: Record<string, string> = {
  ok: "",
  insufficient_baseline: "insufficient baseline",
  insufficient_data: "insufficient data",
  source_error: "source error",
};
const HEADLINES: Record<string, string> = {
  worsening: "Getting worse: more anomalies in the last 7 days",
  improving: "Improving: fewer anomalies in the last 7 days",
  stable: "Stable over the last two weeks",
  inconclusive: "Not enough history to call a trend",
};

type EpisodeLike = { family: string; entity: { key: string; display_name: string } };

const { report } = useReportContext();
const route = useRoute();
const router = useRouter();
const measure = ref<Measure>("anomalous_share");

const category = computed(() => (route.query.category as string) || "");
const entity = computed(() => (route.query.entity as string) || "");
const day = computed(() => (route.query.day !== undefined ? Number(route.query.day) : null));

/** Filtering recomputes episode counts per day from the stored episode list. */
const trends = computed<DailyTrend[]>(() =>
  report.value.trends.map((t) => {
    if (!category.value && !entity.value) return t;

    const episodes = t.episodes.filter(matchesFilters);
    const minutes = episodes.reduce((acc, e) => acc + (new Date(e.end).getTime() - new Date(e.start).getTime()) / MS_PER_MINUTE, 0);
    return {
      ...t,
      episodes,
      episode_count: episodes.length,
      anomalous_minutes: Math.round(minutes),
      affected_entities: [...new Set(episodes.map((e) => e.entity.key))],
      anomalous_share: t.observed_entity_minutes ? Math.min(1, minutes / t.observed_entity_minutes) : null,
    };
  }),
);
const ordered = computed(() => [...trends.value].sort((a, b) => b.bucket_index - a.bucket_index));
const selectedDay = computed(() => trends.value.find((t) => t.bucket_index === day.value) ?? null);
const families = computed(() => [...new Set(report.value.trends.flatMap((t) => t.episodes.map((e) => e.family)))]);
const truncated = computed(() => report.value.trends.some((t) => t.episode_count > t.episodes.length));

const recurring = computed(() => {
  const byEntitySignal = new Map<string, { name: string; signal: string; family: string; days: Set<number> }>();
  for (const t of trends.value) {
    for (const e of t.episodes) {
      const key = `${e.entity.key}|${e.signal}`;
      const item = byEntitySignal.get(key) ?? { name: e.entity.display_name, signal: e.signal, family: e.family, days: new Set<number>() };
      item.days.add(t.bucket_index);
      byEntitySignal.set(key, item);
    }
  }
  return [...byEntitySignal.values()].filter((d) => d.days.size >= MIN_RECURRING_DAYS).sort((a, b) => b.days.size - a.days.size);
});

const tableRows = computed(() =>
  ordered.value.map((t) => [
    dayLabel(t), t.status.replaceAll("_", " "), t.episode_count, t.anomalous_minutes,
    formatShare(t.anomalous_share),
    t.affected_entities.length, t.observed_entity_minutes, formatCoverage(t.coverage), t.baseline_days_used,
  ]),
);

const bars = computed(() => {
  const measureDefinition = MEASURES[measure.value];
  const values = ordered.value.map((t) => (t.status === "ok" ? Number(measureDefinition.format(t) ?? 0) : 0));
  const max = Math.max(...values, 0);
  const worstBucket = max > 0 ? ordered.value[values.indexOf(max)]?.bucket_index : undefined;

  return ordered.value.map((t, i) => {
    const value = values[i] ?? 0;
    return {
      t,
      height: barHeight(t, value, max),
      kind: barKind(t, value, worstBucket),
      covered: t.coverage >= FULL_COVERAGE_RATIO,
    };
  });
});

const latestIsWorst = computed(() => {
  const ok = report.value.trends.filter((t) => t.status === "ok" && t.anomalous_share !== null);
  const latest = ok.find((t) => t.bucket_index === 0);
  return !!latest && (latest.anomalous_share ?? 0) > 0 && ok.every((t) => (t.anomalous_share ?? 0) <= (latest.anomalous_share ?? 0));
});

/** Recurring rows get a 14-cell strip, oldest day first. */
const stripDays = computed(() => ordered.value.map((t) => t.bucket_index));

function setQuery(key: string, value: string | number | null) {
  void router.replace({ query: { ...route.query, [key]: value === null || value === "" ? undefined : String(value) } });
}

function matchesFilters(e: EpisodeLike): boolean {
  return (
    (!category.value || e.family === category.value) &&
    (!entity.value || e.entity.display_name.toLowerCase().includes(entity.value.toLowerCase()))
  );
}

/** A share in [0, 1] as a percentage with two decimals. */
const formatShare = (share: number | null | undefined) => (share === null || share === undefined ? NO_VALUE : `${(share * 100).toFixed(2)} %`);
const formatCoverage = (coverage: number) => `${Math.round(coverage * 100)} %`;

function measureValue(t: DailyTrend): string | undefined {
  if (t.status !== "ok") return STATUS_TEXT[t.status];
  if (measure.value === "anomalous_share") return formatShare(t.anomalous_share);

  const value = MEASURES[measure.value].format(t);
  return value === null ? NO_VALUE : String(value);
}

function barHeight(t: DailyTrend, value: number, max: number): string {
  // Incomplete days are drawn full height (hatched) so they are not mistaken for quiet ones.
  if (t.status !== "ok") return `${BAR_MAX_PX}px`;
  if (value <= 0) return `${BAR_ZERO_PX}px`;
  return `${Math.max(BAR_MIN_PX, Math.round((value / max) * BAR_MAX_PX))}px`;
}

function barKind(t: DailyTrend, value: number, worstBucket: number | undefined): string {
  if (t.status !== "ok") return "incomplete";
  if (value === 0) return "zero";
  return t.bucket_index === worstBucket ? "worst" : "some";
}

const findingLink = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
</script>

<template>
  <div class="trends">
    <section class="hero" aria-label="Trend summary">
      <div class="verdict">
        <h1 v-if="report.trend_summary">{{ HEADLINES[report.trend_summary.direction] ?? report.trend_summary.direction }}</h1>
        <p v-if="latestIsWorst" class="lede">The latest day is the worst of the 14.</p>
        <p v-if="report.trend_summary" class="muted small">
          Detector: <strong>{{ report.trend_summary.direction }}</strong> ({{ report.trend_summary.confidence }} confidence) —
          {{ report.trend_summary.reason }}
        </p>
      </div>
      <template v-if="report.trend_summary">
        <div class="card stat">
          <span class="label-caps">Anomalous share</span>
          <span><strong>{{ formatShare(report.trend_summary.recent_share) }}</strong> <span class="muted">last 7 d</span></span>
          <span class="muted small">vs {{ formatShare(report.trend_summary.previous_share) }} the 7 days before</span>
        </div>
        <div class="card stat">
          <span class="label-caps">Episodes</span>
          <span><strong>{{ report.trend_summary.recent_episodes }}</strong> <span class="muted">last 7 d</span></span>
          <span class="muted small">vs {{ report.trend_summary.previous_episodes }} the 7 days before</span>
        </div>
      </template>
    </section>

    <div class="columns">
      <section class="card chart" aria-labelledby="per-day-title">
        <div class="chart-head">
          <h2 id="per-day-title">{{ MEASURES[measure].label }} per day</h2>
          <div class="seg" role="group" aria-label="Measure">
            <button
              v-for="(m, k) in MEASURES"
              :key="k"
              type="button"
              :aria-pressed="measure === k"
              @click="measure = k"
            >
              {{ m.label }}
            </button>
          </div>
        </div>
        <div class="row filters">
          <label>Category
            <select :value="category" @change="setQuery('category', ($event.target as HTMLSelectElement).value)">
              <option value="">All</option>
              <option v-for="fam in families" :key="fam" :value="fam">{{ FAMILY_LABELS[fam] ?? fam }}</option>
            </select>
          </label>
          <label>Entity
            <input type="search" :value="entity" placeholder="filter by name" @input="setQuery('entity', ($event.target as HTMLInputElement).value)">
          </label>
        </div>
        <div class="days" role="group" aria-label="Select a day">
          <button
            v-for="b in bars"
            :key="b.t.bucket_index"
            type="button"
            :aria-pressed="day === b.t.bucket_index"
            :title="`Coverage ${formatCoverage(b.t.coverage)} · baseline ${b.t.baseline_days_used} days`"
            :class="b.kind"
            @click="setQuery('day', day === b.t.bucket_index ? null : b.t.bucket_index)"
          >
            <span class="v">{{ measureValue(b.t) }}</span>
            <span class="bar" :style="{ height: b.height }" aria-hidden="true" />
            <span class="cov" :class="{ low: !b.covered }" aria-hidden="true" />
            <span class="d">{{ dayLabel(b.t) }}</span>
          </button>
        </div>
        <p class="muted small">
          Select a day to see its episodes. The thin strip is coverage (amber below 95 %); hatched days lack a baseline or data.
          Anomalous share is normalised by observed entity-time, so added hosts and gaps do not look like deterioration.
        </p>
        <DataTable
          caption="Daily trend values"
          :columns="['Day', 'Status', 'Episodes', 'Anomalous min', 'Share', 'Entities', 'Observed entity-min', 'Coverage', 'Baseline days']"
          :rows="tableRows"
        />
      </section>

      <section class="card day" aria-live="polite" aria-labelledby="day-title">
        <template v-if="selectedDay">
          <h2 id="day-title">
            {{ dayLabel(selectedDay) }}:
            {{ selectedDay.episode_count }} episode(s)
            <span v-if="selectedDay.status !== 'ok'" class="muted">— {{ STATUS_TEXT[selectedDay.status] }}</span>
          </h2>
          <p class="muted small">
            <span :title="utcTooltip(selectedDay.window.start)">{{ formatTime(selectedDay.window.start) }}</span> –
            {{ formatTime(selectedDay.window.end) }} · baseline {{ selectedDay.baseline_days_used }} days · coverage {{ formatCoverage(selectedDay.coverage) }}
          </p>
          <p v-if="selectedDay.episodes.length === 0" class="muted">No episodes on this day.</p>
          <ul v-else class="episodes">
            <li v-for="e in selectedDay.episodes" :key="e.episode_id">
              <div class="row">
                <SeverityChip :severity="e.severity" />
                <RouterLink v-if="e.finding_id" :to="findingLink(e.finding_id)">{{ e.signal }}</RouterLink>
                <strong v-else>{{ e.signal }}</strong>
              </div>
              <span class="mono small muted">{{ e.entity.display_name }}</span>
              <span class="small muted">
                {{ formatTime(e.start, false) }}–{{ formatTime(e.end, false) }} · peak {{ formatValue(e.peak_observed, e.unit) }}<template v-if="e.expected_median !== null"> vs {{ formatValue(e.expected_median, e.unit) }}</template>
              </span>
            </li>
          </ul>
          <p v-if="!selectedDay.episodes.some((e) => e.finding_id) && selectedDay.bucket_index > 0" class="muted small">
            Earlier days keep episode summaries only; full evidence charts exist for latest-day findings.
          </p>
        </template>
        <template v-else>
          <h2 id="day-title">Pick a day</h2>
          <p class="muted">Select a bar to list that day's episodes.</p>
        </template>
        <p v-if="truncated" class="muted small">Some days list only part of their episodes (counts are complete).</p>
      </section>
    </div>

    <section class="card" aria-labelledby="recurring-title">
      <h2 id="recurring-title">Recurring (episodes on ≥ 3 of 14 days)</h2>
      <p v-if="recurring.length === 0" class="muted">Nothing recurred on three or more days.</p>
      <ul v-else class="recurring">
        <li v-for="r in recurring" :key="`${r.name}|${r.signal}`">
          <span class="who">
            <strong>{{ r.signal }}</strong>
            <span class="mono small muted">{{ r.name }}</span>
          </span>
          <span><strong>{{ r.days.size }}</strong> of 14 days</span>
          <span class="strip" :aria-label="`Days with episodes: ${r.days.size} of 14`">
            <span v-for="d in stripDays" :key="d" :class="{ on: r.days.has(d) }" />
          </span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.trends { display: flex; flex-direction: column; gap: calc(var(--space) * 3); }
.trends h2 { margin: 0; }
.hero { display: flex; flex-wrap: wrap; gap: calc(var(--space) * 2); align-items: stretch; }
.verdict { flex: 2 1 min(480px, 100%); display: flex; flex-direction: column; gap: 8px; justify-content: center; }
.verdict h1 { margin: 0; font-size: 2rem; line-height: 1.15; letter-spacing: -0.01em; text-wrap: pretty; }
.verdict p { margin: 0; }
.lede { font-size: 1.05rem; color: var(--text-2); }
.stat { flex: 1 1 220px; display: flex; flex-direction: column; gap: 4px; }
.stat strong { font-size: 1.75rem; }
.columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(520px, 100%), 1fr)); gap: calc(var(--space) * 2); align-items: start; }
@media (min-width: 1100px) { .columns { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); } }
.chart, .day { display: flex; flex-direction: column; gap: 12px; }
.chart-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.seg { display: flex; flex-wrap: wrap; }
.seg button { border-radius: 0; border-left-width: 0; background: var(--bg); font-size: 0.8rem; }
.seg button:first-child { border-left-width: 1px; border-radius: var(--radius) 0 0 var(--radius); }
.seg button:last-child { border-radius: 0 var(--radius) var(--radius) 0; }
.seg button[aria-pressed="true"] { background: var(--inv-bg); color: var(--inv-fg); border-color: var(--inv-bg); }
.filters label { display: flex; flex-direction: column; font-size: 0.85rem; color: var(--text-muted); }
.filters { align-items: flex-end; }
.days { display: flex; gap: 4px; align-items: stretch; border-bottom: 1px solid var(--border); overflow-x: auto; }
.days button {
  flex: 1 1 0; min-width: 34px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 4px;
  height: 230px; padding: 4px 2px 6px; border: none; border-radius: var(--radius); background: transparent; color: var(--text-muted); font-size: 0.7rem;
}
.days button:hover { background: var(--surface); }
.days button[aria-pressed="true"] { background: var(--info-bg); color: var(--link-strong); font-weight: 600; outline: 2px solid var(--focus); }
.days .v { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.days .bar { display: block; width: 70%; max-width: 40px; border-radius: 3px 3px 0 0; background: var(--bar-medium); }
.days .zero .bar { background: var(--border); }
.days .worst .bar { background: var(--bar-critical); }
.days .incomplete .bar { background: repeating-linear-gradient(45deg, transparent 0 6px, var(--border) 6px 7px); border: 1px dashed var(--border); }
.days .cov { display: block; width: 100%; height: 4px; border-radius: 2px; background: var(--status-ok); }
.days .cov.low { background: var(--status-warn); }
.days .d { white-space: nowrap; }
.episodes { list-style: none; margin: 0; padding: 0; }
.episodes li { display: flex; flex-direction: column; gap: 4px; padding: 12px 0; border-top: 1px solid var(--subtle); }
.recurring { list-style: none; margin: 12px 0 0; padding: 0; }
.recurring li { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 2fr); gap: 16px; align-items: center; padding: 12px 0; border-top: 1px solid var(--subtle); }
.who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.strip { display: flex; gap: 3px; }
.strip span { flex: 1 1 0; height: 18px; border-radius: 3px; background: var(--subtle); }
.strip .on { background: var(--bar-medium); }
.mono { font-family: var(--font-mono); }
.small { font-size: 0.8rem; }
@media (max-width: 700px) {
  .recurring li { grid-template-columns: minmax(0, 1fr); gap: 6px; }
}
</style>
