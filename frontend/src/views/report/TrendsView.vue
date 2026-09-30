<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import ChartBox from "../../components/ChartBox.vue";
import DataTable from "../../components/DataTable.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { MEASURES, dayLabel, trendOption, type Measure } from "../../lib/charts";
import { FAMILY_LABELS, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";

const { report } = useReportContext();
const route = useRoute();
const router = useRouter();
const measure = ref<Measure>("anomalous_share");

const category = computed(() => (route.query.category as string) || "");
const entity = computed(() => (route.query.entity as string) || "");
const day = computed(() => (route.query.day !== undefined ? Number(route.query.day) : null));
function setQuery(key: string, value: string | number | null) {
  void router.replace({ query: { ...route.query, [key]: value === null || value === "" ? undefined : String(value) } });
}

const keep = (e: { family: string; entity: { key: string; display_name: string } }) =>
  (!category.value || e.family === category.value) &&
  (!entity.value || e.entity.display_name.toLowerCase().includes(entity.value.toLowerCase()));

/** Filtering recomputes episode counts per day from the stored episode list. */
const trends = computed(() =>
  report.value.trends.map((t) => {
    if (!category.value && !entity.value) return t;
    const episodes = t.episodes.filter(keep);
    const minutes = episodes.reduce((acc, e) => acc + (new Date(e.end).getTime() - new Date(e.start).getTime()) / 60000, 0);
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
  const days = new Map<string, { name: string; signal: string; family: string; days: Set<number> }>();
  for (const t of trends.value) {
    for (const e of t.episodes) {
      const key = `${e.entity.key}|${e.signal}`;
      const item = days.get(key) ?? { name: e.entity.display_name, signal: e.signal, family: e.family, days: new Set<number>() };
      item.days.add(t.bucket_index);
      days.set(key, item);
    }
  }
  return [...days.values()].filter((d) => d.days.size >= 3).sort((a, b) => b.days.size - a.days.size);
});

const statusText: Record<string, string> = {
  ok: "",
  insufficient_baseline: "insufficient baseline",
  insufficient_data: "insufficient data",
  source_error: "source error",
};
const measureValue = (t: (typeof trends.value)[number]) => {
  const v = MEASURES[measure.value].format(t);
  if (t.status !== "ok") return statusText[t.status];
  return v === null ? "—" : measure.value === "anomalous_share" ? `${(v as number).toFixed(2)} %` : String(v);
};
const tableRows = computed(() =>
  ordered.value.map((t) => [
    dayLabel(t), t.status.replaceAll("_", " "), t.episode_count, t.anomalous_minutes,
    t.anomalous_share === null ? "—" : `${(t.anomalous_share * 100).toFixed(2)} %`,
    t.affected_entities.length, t.observed_entity_minutes, `${Math.round(t.coverage * 100)} %`, t.baseline_days_used,
  ]),
);
const findingLink = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
</script>

<template>
  <div class="stack">
    <section class="card" aria-label="Trend summary">
      <template v-if="report.trend_summary">
        <p>
          <strong>{{ report.trend_summary.direction }}</strong> ({{ report.trend_summary.confidence }} confidence):
          {{ report.trend_summary.reason }}
        </p>
      </template>
      <p class="muted small">
        Days are 24-hour buckets ending at the report's end time. Each day's baseline uses only the preceding days.
        Anomalous share is normalised by observed entity-time, so added hosts and gaps do not look like deterioration.
      </p>
    </section>

    <div class="row filters">
      <label>Measure
        <select v-model="measure">
          <option v-for="(m, k) in MEASURES" :key="k" :value="k">{{ m.label }}</option>
        </select>
      </label>
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

    <ChartBox
      :option="trendOption(trends, measure, day)"
      :label="`${MEASURES[measure].label} over 14 days`"
      height="300px"
      @click="(p) => { const t = ordered[p.dataIndex]; if (t) setQuery('day', t.bucket_index); }"
    />

    <div class="days" role="group" aria-label="Select a day">
      <button
        v-for="t in ordered"
        :key="t.bucket_index"
        type="button"
        :aria-pressed="day === t.bucket_index"
        :class="{ incomplete: t.status !== 'ok' }"
        @click="setQuery('day', day === t.bucket_index ? null : t.bucket_index)"
      >
        <span class="d">{{ dayLabel(t) }}</span>
        <span class="v">{{ measureValue(t) }}</span>
        <span class="c">cov {{ Math.round(t.coverage * 100) }} %</span>
      </button>
    </div>
    <DataTable
      caption="Daily trend values"
      :columns="['Day', 'Status', 'Episodes', 'Anomalous min', 'Share', 'Entities', 'Observed entity-min', 'Coverage', 'Baseline days']"
      :rows="tableRows"
    />

    <section v-if="selectedDay" aria-labelledby="day-title">
      <h2 id="day-title">
        {{ dayLabel(selectedDay) }}:
        {{ selectedDay.episode_count }} episode(s)
        <span v-if="selectedDay.status !== 'ok'" class="muted">— {{ statusText[selectedDay.status] }}</span>
      </h2>
      <p class="muted small">
        <span :title="utcTooltip(selectedDay.window.start)">{{ formatTime(selectedDay.window.start) }}</span> –
        {{ formatTime(selectedDay.window.end) }} · baseline {{ selectedDay.baseline_days_used }} days · coverage {{ Math.round(selectedDay.coverage * 100) }} %
      </p>
      <p v-if="selectedDay.episodes.length === 0" class="muted">No episodes on this day.</p>
      <div v-else class="table-wrap">
        <table class="table">
          <thead><tr><th scope="col">Severity</th><th scope="col">Signal</th><th scope="col">Entity</th><th scope="col">Time</th><th scope="col">Peak</th></tr></thead>
          <tbody>
            <tr v-for="e in selectedDay.episodes" :key="e.episode_id">
              <td><SeverityChip :severity="e.severity" /></td>
              <td>
                <RouterLink v-if="e.finding_id" :to="findingLink(e.finding_id)">{{ e.signal }}</RouterLink>
                <span v-else>{{ e.signal }}</span>
              </td>
              <td class="mono">{{ e.entity.display_name }}</td>
              <td>{{ formatTime(e.start, false) }}–{{ formatTime(e.end, false) }}</td>
              <td>{{ formatValue(e.peak_observed, e.unit) }}<span v-if="e.expected_median !== null" class="muted"> vs {{ formatValue(e.expected_median, e.unit) }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!selectedDay.episodes.some((e) => e.finding_id) && selectedDay.bucket_index > 0" class="muted small">
        Earlier days keep episode summaries only; full evidence charts exist for latest-day findings.
      </p>
    </section>
    <p v-if="truncated" class="muted small">Some days list only part of their episodes (counts are complete).</p>

    <section aria-labelledby="recurring-title">
      <h2 id="recurring-title">Recurring (episodes on ≥ 3 of 14 days)</h2>
      <p v-if="recurring.length === 0" class="muted">Nothing recurred on three or more days.</p>
      <div v-else class="table-wrap">
        <table class="table">
          <thead><tr><th scope="col">Entity</th><th scope="col">Signal</th><th scope="col">Days</th></tr></thead>
          <tbody>
            <tr v-for="r in recurring" :key="`${r.name}|${r.signal}`">
              <td class="mono">{{ r.name }}</td><td>{{ r.signal }}</td><td>{{ r.days.size }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.filters label { display: flex; flex-direction: column; font-size: 0.85rem; }
.filters { align-items: flex-end; }
.days { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 4px; }
.days button { display: flex; flex-direction: column; align-items: flex-start; font-size: 0.8rem; padding: 4px 6px; }
.days button[aria-pressed="true"] { outline: 2px solid var(--focus); }
.days .incomplete { background-image: repeating-linear-gradient(45deg, transparent 0 6px, var(--border) 6px 7px); }
.days .v { font-weight: 600; }
.days .c { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: 0.85rem; }
.small { font-size: 0.85rem; }
</style>
