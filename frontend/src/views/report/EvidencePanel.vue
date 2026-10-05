<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import type { RouteLocationRaw } from "vue-router";
import type { Finding } from "../../api/client";
import ChartBox from "../../components/ChartBox.vue";
import DataTable from "../../components/DataTable.vue";
import ExplanationPanel from "../../components/ExplanationPanel.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { evidenceOption, timestamps } from "../../lib/charts";
import { headline, recurrenceText } from "../../lib/findings";
import { capitalize, formatDuration, formatTime, formatValue, isLatencyMean, timezone, utcTooltip } from "../../lib/format";
import { chartPalette } from "../../lib/theme";
import { useReportContext } from "./context";

const EVIDENCE_COLUMNS = ["Time", "Observed", "Expected", "Lower", "Upper", "Gap"];
const SECONDS_PER_MINUTE = 60;

const props = defineProps<{ finding: Finding; closeTo: RouteLocationRaw }>();

const { report, evidenceById } = useReportContext();
const heading = ref<HTMLElement | null>(null);
const copied = ref(false);

const f = computed(() => props.finding);
const evidence = computed(() =>
  f.value.evidence_ids
    .map((id) => evidenceById.value.get(id))
    .filter((e) => !!e),
);
const primaryEvidence = computed(() => evidence.value[0] ?? null);
const stepMinutes = computed(() => Math.round((primaryEvidence.value?.series.step_seconds ?? 0) / SECONDS_PER_MINUTE));
const rows = computed(() => {
  const e = primaryEvidence.value;
  if (!e) return [];

  const unit = e.series.unit;
  return timestamps(e).map((t, i) => [
    formatTime(t),
    formatValue(e.series.values[i], unit),
    formatValue(e.expected?.[i], unit),
    formatValue(e.lower?.[i], unit),
    formatValue(e.upper?.[i], unit),
    e.series.values[i] === null ? "gap" : "",
  ]);
});

const findingLink = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;

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
  <section class="dash-sub" aria-labelledby="evidence-title">
    <section class="panel s16" aria-label="Evidence chart">
      <div class="ph">
        <SeverityChip :severity="f.severity" />
        <h2 id="evidence-title" ref="heading" tabindex="-1">{{ f.title }}</h2>
        <span class="sub mono">· {{ f.entity.display_name }}</span>
        <RouterLink :to="closeTo" class="close" aria-label="Close evidence" title="Close (Esc)">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8" /></svg>
        </RouterLink>
      </div>
      <div class="pb">
        <template v-if="primaryEvidence">
          <ChartBox :option="evidenceOption(primaryEvidence, f, chartPalette)" :label="`Evidence chart for ${f.title}`" height="380px" />
          <div class="caption">
            <span class="lbl">
              {{ timezone === "utc" ? "UTC" : "Local time" }} · {{ stepMinutes }}-min steps
              <template v-if="f.expected"> · usual = median ± scaled MAD, {{ f.baseline_days }}-day baseline</template>
            </span>
          </div>
          <DataTable :caption="`Evidence values for ${f.title}`" :columns="EVIDENCE_COLUMNS" :rows="rows" />
        </template>
        <p v-else class="banner">Evidence for this finding was not stored (see report exclusions).</p>
      </div>
    </section>

    <section class="panel s8" aria-labelledby="details-title">
      <div class="ph"><h2 id="details-title">Finding details</h2></div>
      <div class="pb details">
        <div class="row">
          <span class="tag" :class="{ new: f.recurrence === 'new' }">{{ recurrenceText(f) }}</span>
          <span class="tag">Confidence: {{ f.confidence }}</span>
          <span class="tag">{{ f.state === "ongoing" ? "Ongoing" : "Resolved" }}</span>
        </div>
        <h3 class="headline">{{ headline(f) }}</h3>
        <dl class="stats">
          <div><dt class="lbl">Peak</dt><dd :class="`v-${f.severity}`">{{ formatValue(f.observed.value, f.observed.unit) }}</dd></div>
          <div v-if="f.expected">
            <dt class="lbl">Usual</dt>
            <dd class="small">{{ formatValue(f.expected.lower, f.expected.unit) }}–{{ formatValue(f.expected.upper, f.expected.unit) }}</dd>
          </div>
          <div v-else-if="f.threshold !== null">
            <dt class="lbl">Heuristic</dt>
            <dd class="small">{{ formatValue(f.threshold, f.observed.unit) }}</dd>
          </div>
          <div><dt class="lbl">Duration</dt><dd>{{ formatDuration(f.duration_seconds) }}</dd></div>
        </dl>
        <dl class="kv">
          <dt>Window</dt>
          <dd><span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span> – <span :title="utcTooltip(f.end)">{{ formatTime(f.end) }}</span></dd>
          <dt>Peak at</dt>
          <dd :title="utcTooltip(f.peak_at)">{{ formatTime(f.peak_at) }}</dd>
          <template v-if="f.expected">
            <dt>Expected</dt>
            <dd>median {{ formatValue(f.expected.median, f.expected.unit) }}</dd>
          </template>
          <template v-if="f.threshold !== null">
            <dt>Heuristic</dt>
            <dd>{{ formatValue(f.threshold, f.observed.unit) }} (diagnostic, not an SLO)</dd>
          </template>
          <dt>Detector</dt>
          <dd class="mono small">{{ f.detector_version }}</dd>
          <dt>Baseline</dt>
          <dd>
            <template v-if="f.baseline_days !== null">{{ f.baseline_days }} days{{ f.baseline_mode === "time_of_day" ? ", time-of-day" : "" }}</template>
            <template v-else>none (absolute check)</template>
          </dd>
        </dl>
        <p v-if="isLatencyMean(f.signal)" class="banner">
          Mean latency from <code>_sum/_count</code>: histogram buckets are not available, so percentiles cannot be shown.
        </p>
        <p v-if="f.attributes?.latency_bound" class="banner">
          Latency at the top histogram bucket: the true value may be higher ({{ f.attributes.latency_bound }}).
        </p>
        <div v-if="f.confidence_reasons.length" class="small">
          Confidence lowered by:
          <ul class="reasons">
            <li v-for="r in f.confidence_reasons" :key="r.code">{{ r.message }}</li>
          </ul>
        </div>

        <ExplanationPanel class="ai" :report="report" :finding-link="findingLink" :only="f.finding_id" />
      </div>
    </section>

    <section class="panel s24" aria-labelledby="query-title">
      <div class="ph">
        <h2 id="query-title">Query inspector</h2>
        <span v-if="primaryEvidence" class="sub">· step {{ stepMinutes }}m · from {{ formatTime(primaryEvidence.series.start) }}</span>
      </div>
      <div class="pb">
        <details open>
          <summary>Query</summary>
          <div v-for="e in evidence" :key="e.evidence_id" class="query">
            <p class="lbl mono">{{ e.series.signal }} · step {{ e.series.step_seconds }} s</p>
            <pre class="code">{{ e.series.query }}</pre>
            <div class="row">
              <button type="button" @click="copyQuery(e.series.query)">Copy query</button>
              <span v-if="copied" role="status" class="lbl">Query copied.</span>
            </div>
          </div>
        </details>
        <details>
          <summary>Labels and detector</summary>
          <dl class="kv labels">
            <template v-for="(v, k) in f.entity.labels" :key="k">
              <dt class="mono">{{ k }}</dt>
              <dd class="mono">{{ v }}</dd>
            </template>
            <template v-for="(v, k) in f.attributes ?? {}" :key="`a-${k}`">
              <dt class="mono">{{ k }}</dt>
              <dd class="mono">{{ v }}</dd>
            </template>
          </dl>
          <p class="lbl">
            Detector {{ f.detector }} ({{ f.detector_version }})
            <template v-if="f.peak_score !== null"> · robust z {{ f.peak_score.toFixed(1) }}</template>
            · method {{ capitalize(f.method) }}
          </p>
        </details>
      </div>
    </section>
  </section>
</template>

<style scoped>
h2:focus { outline: none; }
h2:focus-visible { outline: 2px solid var(--primary); }
.close { margin-left: auto; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; border-radius: var(--radius); color: var(--muted); }
.close:hover { background: var(--hover); color: var(--strong); }
.caption { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.details { gap: 14px; }
.headline { margin: 0; font-size: 18px; font-weight: 600; line-height: 1.3; }
.stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; margin: 0; }
.stats div { background: var(--canvas); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; }
.stats dd { margin: 0; font-size: 20px; font-weight: 600; color: var(--strong); line-height: 30px; }
.stats dd.small { font-size: 15px; }
.stats dd.v-critical { color: var(--crit); }
.stats dd.v-high { color: var(--high); }
.stats dd.v-medium { color: var(--med); }
.small { font-size: 12px; }
.reasons { margin: 4px 0 0; padding-left: 20px; }
.ai { padding-top: 10px; border-top: 1px solid var(--border); }
details summary { cursor: pointer; font-size: 13px; font-weight: 500; color: var(--strong); }
details > * + * { margin-top: 8px; }
.query { display: flex; flex-direction: column; gap: 6px; }
.labels { margin-top: 8px; }
</style>
