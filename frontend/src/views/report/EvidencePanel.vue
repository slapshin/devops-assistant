<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import type { RouteLocationRaw } from "vue-router";
import type { Finding } from "../../api/client";
import ChartBox from "../../components/ChartBox.vue";
import ConfidenceBadge from "../../components/ConfidenceBadge.vue";
import DataTable from "../../components/DataTable.vue";
import ExplanationPanel from "../../components/ExplanationPanel.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { evidenceOption, timestamps } from "../../lib/charts";
import { formatDuration, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";

const props = defineProps<{ finding: Finding; closeTo: RouteLocationRaw }>();
const { report, evidenceById, findingById } = useReportContext();
const heading = ref<HTMLElement | null>(null);
const copied = ref(false);

const f = computed(() => props.finding);
const evidence = computed(() => f.value.evidence_ids.map((id) => evidenceById.value.get(id)).filter((e) => !!e));
const main = computed(() => evidence.value[0] ?? null);
const related = computed(() => (f.value.related_finding_ids ?? []).map((id) => findingById.value.get(id)).filter((x) => !!x));
const link = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;

const rows = computed(() => {
  const e = main.value;
  if (!e) return [];
  return timestamps(e).map((t, i) => [
    formatTime(t),
    formatValue(e.series.values[i], e.series.unit),
    formatValue(e.expected?.[i], e.series.unit),
    formatValue(e.lower?.[i], e.series.unit),
    formatValue(e.upper?.[i], e.series.unit),
    e.series.values[i] === null ? "gap" : "",
  ]);
});

async function copyQuery(query: string) {
  try {
    await navigator.clipboard.writeText(query);
    copied.value = true;
  } catch {
    copied.value = false;
  }
}

onMounted(async () => {
  await nextTick();
  heading.value?.focus();
});
</script>

<template>
  <section class="panel stack" aria-labelledby="evidence-title">
    <RouterLink :to="closeTo" class="back">← Findings</RouterLink>
    <h2 id="evidence-title" ref="heading" tabindex="-1">{{ f.title }}</h2>
    <div class="row">
      <SeverityChip :severity="f.severity" />
      <ConfidenceBadge :confidence="f.confidence" />
      <span class="muted">{{ f.state === "ongoing" ? "Ongoing" : "Resolved" }} · {{ f.recurrence }}</span>
    </div>
    <p class="entity">{{ f.entity.display_name }}</p>
    <p>
      <span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span> – <span :title="utcTooltip(f.end)">{{ formatTime(f.end) }}</span>
      ({{ formatDuration(f.duration_seconds) }}), peak at {{ formatTime(f.peak_at) }}
    </p>
    <p>
      Observed peak <strong>{{ formatValue(f.observed.value, f.observed.unit) }}</strong>
      <template v-if="f.expected">
        · expected {{ formatValue(f.expected.median, f.expected.unit) }} (normal range {{ formatValue(f.expected.lower, f.expected.unit) }}–{{ formatValue(f.expected.upper, f.expected.unit) }})
      </template>
      <template v-if="f.threshold !== null"> · diagnostic heuristic {{ formatValue(f.threshold, f.observed.unit) }} (not an SLO)</template>
    </p>
    <p v-if="f.signal === 'latency_mean'" class="banner">Mean latency from <code>_sum/_count</code>: histogram buckets are not available, so percentiles cannot be shown.</p>
    <p v-if="f.attributes?.latency_bound" class="banner">Latency at the top histogram bucket: the true value may be higher ({{ f.attributes.latency_bound }}).</p>
    <p class="muted small">
      Detector {{ f.detector }} ({{ f.detector_version }}) ·
      <template v-if="f.baseline_days !== null">baseline {{ f.baseline_days }} days{{ f.baseline_mode === "time_of_day" ? ", time-of-day" : "" }}</template>
      <template v-else>no baseline (absolute check)</template>
      <template v-if="f.peak_score !== null"> · robust z {{ f.peak_score.toFixed(1) }}</template>
    </p>
    <div v-if="f.confidence_reasons.length" class="small">
      Confidence lowered by:
      <ul><li v-for="r in f.confidence_reasons" :key="r.code">{{ r.message }}</li></ul>
    </div>

    <template v-if="main">
      <ChartBox :option="evidenceOption(main, f)" :label="`Evidence chart for ${f.title}`" />
      <DataTable
        :caption="`Evidence values for ${f.title}`"
        :columns="['Time', 'Observed', 'Expected', 'Lower', 'Upper', 'Gap']"
        :rows="rows"
      />
    </template>
    <p v-else class="banner">Evidence for this finding was not stored (see report exclusions).</p>

    <h3>Supporting query</h3>
    <div v-for="e in evidence" :key="e.evidence_id" class="stack">
      <p class="small muted">{{ e.series.signal }} · step {{ e.series.step_seconds }} s · {{ formatTime(e.series.start) }} onward</p>
      <pre>{{ e.series.query }}</pre>
      <button type="button" @click="copyQuery(e.series.query)">Copy query</button>
    </div>
    <p v-if="copied" role="status" class="small">Query copied.</p>

    <h3>Labels</h3>
    <dl class="labels">
      <template v-for="(v, k) in f.entity.labels" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
      <template v-for="(v, k) in f.attributes ?? {}" :key="`a-${k}`"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
    </dl>

    <h3>Related findings</h3>
    <ul v-if="related.length">
      <li v-for="r in related" :key="r.finding_id"><RouterLink :to="link(r.finding_id)">{{ r.title }} — {{ r.entity.display_name }}</RouterLink></li>
    </ul>
    <p v-else class="muted small">None share an identity label or verified host mapping. Time overlap alone is not treated as a relation.</p>

    <ExplanationPanel :report="report" :finding-link="link" :only="f.finding_id" />
  </section>
</template>

<style scoped>
.panel { padding: calc(var(--space) * 1.5); border-left: 1px solid var(--border); }
.entity { font-family: var(--font-mono); }
.labels { display: grid; grid-template-columns: max-content 1fr; gap: 2px var(--space); font-size: 0.85rem; }
.labels dt { color: var(--text-muted); }
.labels dd { margin: 0; font-family: var(--font-mono); word-break: break-all; }
.small { font-size: 0.85rem; }
h2:focus { outline: none; }
h2:focus-visible { outline: 2px solid var(--focus); }
</style>
