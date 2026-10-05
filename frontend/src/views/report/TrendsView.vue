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
/** Week-over-week direction from the sign of the difference. */
const CHANGE_KIND: Record<number, string> = { 1: "up", [-1]: "down", 0: "same" };
const CHANGE_ARROW: Record<string, string> = { up: "▲", down: "▼", same: "=" };
/** An entity/signal pair counts as recurring once it has episodes on this many days. */
const MIN_RECURRING_DAYS = 3;
/** Below this, a day's coverage strip is shown in amber. */
const FULL_COVERAGE_RATIO = 0.95;
/** The day bars are the day buttons: height is the measure relative to the busiest day. */
const BAR_MAX_PX = 160;
const BAR_MIN_PX = 4;
const BAR_ZERO_PX = 2;
const NO_VALUE = "—";
const LATEST_BUCKET = 0;
const TREND_DAYS = 14;

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

const { report, findingById } = useReportContext();
const route = useRoute();
const router = useRouter();
const measure = ref<Measure>("anomalous_share");

const category = computed(() => (route.query.category as string) || "");
const entity = computed(() => (route.query.entity as string) || "");
/** The latest day (bucket 0) is selected until the viewer picks another. */
const day = computed(() => (route.query.day !== undefined ? Number(route.query.day) : LATEST_BUCKET));

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
      kind: barKind(t, value),
      worst: t.bucket_index === worstBucket,
      covered: t.coverage >= FULL_COVERAGE_RATIO,
    };
  });
});
const lowCoverageDays = computed(() => report.value.trends.filter((t) => t.coverage < FULL_COVERAGE_RATIO).length);
const selectedTitle = computed(() => {
  const t = selectedDay.value;
  if (!t) return "";
  const label = t.bucket_index === LATEST_BUCKET ? `Latest day, ${dayLabel(t)}` : dayLabel(t);
  return `${label} · ${t.episode_count} ${t.episode_count === 1 ? "episode" : "episodes"}`;
});
const okBuckets = computed(() => new Set(report.value.trends.filter((t) => t.status === "ok").map((t) => t.bucket_index)));
const weekChange = computed(() => {
  const summary = report.value.trend_summary;
  if (!summary) return null;
  const share = Math.sign((summary.recent_share ?? 0) - (summary.previous_share ?? 0));
  const episodes = Math.sign(summary.recent_episodes - summary.previous_episodes);
  return { share: CHANGE_KIND[share] ?? "same", episodes: CHANGE_KIND[episodes] ?? "same" };
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

/** The latest day is drawn in the critical fill, earlier days in amber (UI design "Today / Earlier days"). */
function barKind(t: DailyTrend, value: number): string {
  if (t.status !== "ok") return "incomplete";
  if (value === 0) return "zero";
  return t.bucket_index === LATEST_BUCKET ? "latest" : "earlier";
}

/** A recurring-strip cell: episode on the latest day, an earlier day, an evaluated quiet day, or a day without data. */
function stripKind(bucket: number, days: Set<number>): string {
  if (days.has(bucket)) return bucket === LATEST_BUCKET ? "latest" : "earlier";
  return okBuckets.value.has(bucket) ? "quiet" : "unknown";
}

const episodeTitle = (e: { finding_id?: string | null; signal: string }) =>
  (e.finding_id ? findingById.value.get(e.finding_id)?.title : undefined) ?? e.signal;

const findingLink = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
</script>

<template>
  <div class="dash">
    <section class="panel s12" aria-labelledby="trend-summary-title">
      <div class="ph"><h2 id="trend-summary-title">Summary</h2><span class="sub">· 14 days</span></div>
      <div class="pb summary">
        <h1 v-if="report.trend_summary">{{ HEADLINES[report.trend_summary.direction] ?? report.trend_summary.direction }}</h1>
        <h1 v-else>No trend summary in this report</h1>
        <p v-if="latestIsWorst" class="lede">The latest day is the worst of the 14.</p>
        <p v-if="report.trend_summary" class="lbl">
          Detector: <strong>{{ report.trend_summary.direction }}</strong> ({{ report.trend_summary.confidence }} confidence) —
          {{ report.trend_summary.reason }}
        </p>
      </div>
    </section>
    <template v-if="report.trend_summary && weekChange">
      <section class="panel s6" aria-label="Anomalous share">
        <div class="ph"><h2>Anomalous share · 7 d</h2></div>
        <div class="stat">
          <div class="v" :class="`c-${weekChange.share}`">{{ formatShare(report.trend_summary.recent_share) }}</div>
          <div class="d" :class="`c-${weekChange.share}`">
            <span aria-hidden="true">{{ CHANGE_ARROW[weekChange.share] }}</span> vs {{ formatShare(report.trend_summary.previous_share) }} the 7 days before
          </div>
        </div>
      </section>
      <section class="panel s6" aria-label="Episodes">
        <div class="ph"><h2>Episodes · 7 d</h2></div>
        <div class="stat">
          <div class="v" :class="`c-${weekChange.episodes}`">{{ report.trend_summary.recent_episodes }}</div>
          <div class="d" :class="`c-${weekChange.episodes}`">
            <span aria-hidden="true">{{ CHANGE_ARROW[weekChange.episodes] }}</span> vs {{ report.trend_summary.previous_episodes }} the 7 days before
          </div>
        </div>
      </section>
    </template>

    <section class="panel s16" aria-labelledby="per-day-title">
      <div class="ph">
        <h2 id="per-day-title">{{ MEASURES[measure].label }} per day</h2>
        <div class="tg end" role="group" aria-label="Measure">
          <button v-for="(m, k) in MEASURES" :key="k" type="button" :aria-pressed="measure === k" @click="measure = k">{{ m.label }}</button>
        </div>
      </div>
      <div class="pb">
        <div class="row">
          <div class="var">
            <label for="t-category">Category</label>
            <select id="t-category" :value="category" @change="setQuery('category', ($event.target as HTMLSelectElement).value)">
              <option value="">All</option>
              <option v-for="fam in families" :key="fam" :value="fam">{{ FAMILY_LABELS[fam] ?? fam }}</option>
            </select>
          </div>
          <div class="var">
            <label for="t-entity">Entity</label>
            <input id="t-entity" type="search" :value="entity" placeholder="filter by name" @input="setQuery('entity', ($event.target as HTMLInputElement).value)">
          </div>
        </div>
        <div class="days" role="group" aria-label="Select a day">
          <button
            v-for="b in bars"
            :key="b.t.bucket_index"
            type="button"
            :aria-pressed="day === b.t.bucket_index"
            :title="`Coverage ${formatCoverage(b.t.coverage)} · baseline ${b.t.baseline_days_used} days`"
            :class="[b.kind, { worst: b.worst }]"
            @click="setQuery('day', b.t.bucket_index === LATEST_BUCKET ? null : b.t.bucket_index)"
          >
            <span class="v">{{ measureValue(b.t) }}</span>
            <span class="bar" :style="{ height: b.height }" aria-hidden="true" />
            <span class="cov" :class="{ low: !b.covered }" aria-hidden="true" />
            <span class="d">{{ dayLabel(b.t) }}</span>
          </button>
        </div>
        <p class="lbl">
          {{ lowCoverageDays ? `Coverage below 95 % on ${lowCoverageDays} of ${report.trends.length} days (amber strip).` : `Coverage: full on all ${report.trends.length} days.` }}
          Hatched days lack a baseline or data and are not quiet days. Anomalous share is normalised by observed entity-time, so added hosts and gaps do not look like deterioration.
        </p>
        <div class="foot">
          <span class="legend">
            <span><span class="sw box latest" />Latest day</span>
            <span><span class="sw box earlier" />Earlier days</span>
            <span><span class="sw box incomplete" />No baseline or data</span>
          </span>
          <DataTable
            caption="Daily trend values"
            :columns="['Day', 'Status', 'Episodes', 'Anomalous min', 'Share', 'Entities', 'Observed entity-min', 'Coverage', 'Baseline days']"
            :rows="tableRows"
          />
        </div>
      </div>
    </section>

    <section class="panel s8" aria-labelledby="day-title">
      <div class="ph"><h2 id="day-title">{{ selectedDay ? selectedTitle : "Pick a day" }}</h2></div>
      <div class="pb day" aria-live="polite">
        <template v-if="selectedDay">
          <p class="lbl">
            <span :title="utcTooltip(selectedDay.window.start)">{{ formatTime(selectedDay.window.start) }}</span> –
            {{ formatTime(selectedDay.window.end) }} · baseline {{ selectedDay.baseline_days_used }} days · coverage {{ formatCoverage(selectedDay.coverage) }}
            <template v-if="selectedDay.status !== 'ok'"> · {{ STATUS_TEXT[selectedDay.status] }}</template>
          </p>
          <p v-if="selectedDay.episodes.length === 0" class="muted">
            No episodes on this day.<template v-if="selectedDay.status === 'ok'"> Signals were evaluated with {{ formatCoverage(selectedDay.coverage) }} coverage.</template>
          </p>
          <ul v-else class="episodes">
            <li v-for="e in selectedDay.episodes" :key="e.episode_id">
              <div class="row">
                <SeverityChip :severity="e.severity" />
                <RouterLink v-if="e.finding_id" :to="findingLink(e.finding_id)" class="t">{{ episodeTitle(e) }}</RouterLink>
                <strong v-else class="t">{{ episodeTitle(e) }}</strong>
              </div>
              <span class="mono e">{{ e.entity.display_name }}</span>
              <span class="lbl">
                {{ formatTime(e.start, false) }}–{{ formatTime(e.end, false) }} · peak {{ formatValue(e.peak_observed, e.unit) }}<template v-if="e.expected_median !== null"> vs {{ formatValue(e.expected_median, e.unit) }}</template>
              </span>
            </li>
          </ul>
          <RouterLink v-if="selectedDay.bucket_index === LATEST_BUCKET && selectedDay.episodes.length" :to="`/reports/${report.analysis_id}/findings`" class="more">
            Open evidence for the latest day's findings →
          </RouterLink>
          <p v-else-if="selectedDay.episodes.length" class="lbl">Earlier days keep stored summaries only, not raw series.</p>
        </template>
        <p v-else class="muted">Select a bar to list that day's episodes.</p>
        <p v-if="truncated" class="lbl">Some days list only part of their episodes (counts are complete).</p>
      </div>
    </section>

    <section class="panel s24" aria-labelledby="recurring-title">
      <div class="ph">
        <h2 id="recurring-title">Recurring problems</h2>
        <span class="sub">· status history · episodes on {{ MIN_RECURRING_DAYS }} or more of the {{ TREND_DAYS }} days</span>
      </div>
      <div class="pb">
        <p v-if="recurring.length === 0" class="muted">Nothing recurred on three or more days.</p>
        <div v-else class="recurring">
          <template v-for="r in recurring" :key="`${r.name}|${r.signal}`">
            <div class="who">
              <strong>{{ r.signal }}</strong>
              <span class="mono e">{{ r.name }}</span>
            </div>
            <div class="strip" role="img" :aria-label="`Days with episodes: ${r.days.size} of ${TREND_DAYS}`">
              <span v-for="d in stripDays" :key="d" :class="stripKind(d, r.days)" />
            </div>
            <div class="num"><strong>{{ r.days.size }}</strong> / {{ TREND_DAYS }} days</div>
          </template>
          <div />
          <div class="strip labels" aria-hidden="true">
            <span v-for="t in ordered" :key="t.bucket_index">{{ dayLabel(t).split(" ")[0] }}</span>
          </div>
          <div />
        </div>
        <div class="legend">
          <span><span class="sw box latest" />Episode on the latest day</span>
          <span><span class="sw box earlier" />Episode on an earlier day</span>
          <span><span class="sw box quiet" />Evaluated, no episode</span>
          <span><span class="sw box unknown" />No baseline or data</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.summary { gap: 8px; padding: 8px 20px 16px; justify-content: center; }
.summary h1 { margin: 0; text-wrap: pretty; }
.lede { font-size: 15px; }
.stat .c-up { color: var(--crit); }
.stat .c-down { color: var(--ok); }
.days { display: flex; gap: 2px; align-items: stretch; border-bottom: 1px solid var(--border2); overflow-x: auto; }
.days button {
  flex: 1 1 0; min-width: 34px; min-height: 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 4px;
  height: 230px; padding: 0 1px 4px; border: none; border-radius: var(--radius-sm); background: transparent; color: var(--muted); font-size: 11px; font-weight: 400;
}
.days button:hover { background: var(--hover); }
.days button[aria-pressed="true"] { background: var(--sel); color: var(--strong); font-weight: 600; }
.days .v { font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.days .bar { display: block; width: 72%; max-width: 44px; border-radius: 1px 1px 0 0; }
.days .zero .bar { background: var(--border2); }
.days .cov { display: block; width: 100%; height: 4px; border-radius: 1px; background: var(--f-ok); }
.days .cov.low { background: var(--f-med); }
.days .d { white-space: nowrap; }
.latest .bar, .sw.latest, .strip .latest { background: var(--f-crit); }
.earlier .bar, .sw.earlier, .strip .earlier { background: var(--f-med); }
.incomplete .bar, .sw.incomplete, .strip .unknown, .sw.unknown {
  background: repeating-linear-gradient(45deg, transparent 0 5px, var(--border2) 5px 6px); box-shadow: inset 0 0 0 1px var(--border2);
}
.sw.quiet, .strip .quiet { background: var(--f-ok-dim); }
.days .worst .v { color: var(--strong); font-weight: 600; }
.foot { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.day { gap: 0; }
.day > p { padding: 4px 0; }
.episodes { list-style: none; margin: 0; padding: 0; }
.episodes li { display: flex; flex-direction: column; gap: 4px; padding: 10px 0; border-bottom: 1px solid var(--grid); }
.episodes .t { font-size: 13px; font-weight: 500; color: var(--strong); }
.e { font-size: 12px; color: var(--muted); }
.more { font-size: 13px; padding-top: 10px; }
.recurring { display: grid; grid-template-columns: minmax(160px, 260px) minmax(0, 1fr) 90px; gap: 6px 12px; align-items: center; }
.who { display: flex; flex-direction: column; min-width: 0; font-size: 13px; }
.who strong { font-weight: 500; color: var(--strong); }
.strip { display: flex; gap: 2px; }
.strip span { display: block; flex: 1 1 0; height: 24px; border-radius: 1px; }
.strip.labels span { height: auto; text-align: center; font-size: 10px; color: var(--muted); }
.recurring .num { font-size: 13px; }
@media (max-width: 700px) {
  .recurring { grid-template-columns: minmax(0, 1fr); }
  .strip.labels { display: none; }
}
</style>
