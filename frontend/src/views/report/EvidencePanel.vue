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
import { recurrenceText, timeOverlaps } from "../../lib/findings";
import {
  formatDuration,
  formatTime,
  formatValue,
  isLatencyMean,
  utcTooltip,
} from "../../lib/format";
import { chartPalette } from "../../lib/theme";
import { useReportContext } from "./context";

const EVIDENCE_COLUMNS = ["Time", "Observed", "Expected", "Lower", "Upper", "Gap"];

const props = defineProps<{ finding: Finding; closeTo: RouteLocationRaw }>();

const { report, evidenceById, findingById } = useReportContext();
const heading = ref<HTMLElement | null>(null);
const copied = ref(false);

const f = computed(() => props.finding);
const evidence = computed(() =>
  f.value.evidence_ids
    .map((id) => evidenceById.value.get(id))
    .filter((e) => !!e),
);
const primaryEvidence = computed(() => evidence.value[0] ?? null);
const related = computed(() =>
  (f.value.related_finding_ids ?? [])
    .map((id) => findingById.value.get(id))
    .filter((x) => !!x),
);
/** Time overlap only: shown apart from related findings and never presented as a relation. */
const coincident = computed(() => {
  const relatedIds = new Set(f.value.related_finding_ids ?? []);
  return timeOverlaps(report.value.findings)
    .flatMap((pair) => otherInPair(pair, f.value.finding_id))
    .filter((other) => !relatedIds.has(other.finding_id));
});
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

/** The partner of `findingId` in an overlapping pair, or nothing when it is not part of the pair. */
function otherInPair([a, b]: [Finding, Finding], findingId: string): Finding[] {
  if (a.finding_id === findingId) return [b];
  if (b.finding_id === findingId) return [a];
  return [];
}

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
  <section class="panel card" aria-labelledby="evidence-title">
    <RouterLink :to="closeTo" class="back">← Findings</RouterLink>
    <div class="row">
      <SeverityChip :severity="f.severity" />
      <span class="tag" :class="f.recurrence === 'new' ? 'new' : 'seen'">{{ recurrenceText(f) }}</span>
      <ConfidenceBadge :confidence="f.confidence" />
    </div>
    <h2 id="evidence-title" ref="heading" tabindex="-1">{{ f.title }}</h2>
    <p class="entity">
      {{ f.entity.display_name }} ·
      <span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span> –
      <span :title="utcTooltip(f.end)">{{ formatTime(f.end) }}</span> ·
      {{ f.state === "ongoing" ? "ongoing" : "resolved" }}
    </p>

    <dl class="stats">
      <div><dt class="label-caps">Peak</dt><dd :class="`sev-${f.severity}`">{{ formatValue(f.observed.value, f.observed.unit) }}</dd></div>
      <div v-if="f.expected">
        <dt class="label-caps">Usual</dt>
        <dd>{{ formatValue(f.expected.lower, f.expected.unit) }}–{{ formatValue(f.expected.upper, f.expected.unit) }}</dd>
      </div>
      <div v-else-if="f.threshold !== null">
        <dt class="label-caps">Heuristic</dt>
        <dd>{{ formatValue(f.threshold, f.observed.unit) }}</dd>
      </div>
      <div><dt class="label-caps">Duration</dt><dd>{{ formatDuration(f.duration_seconds) }}</dd></div>
    </dl>
    <p class="muted small">
      Peak at {{ formatTime(f.peak_at) }}
      <template v-if="f.expected"> · expected median {{ formatValue(f.expected.median, f.expected.unit) }}</template>
      <template v-if="f.threshold !== null"> · diagnostic heuristic {{ formatValue(f.threshold, f.observed.unit) }} (not an SLO)</template>
    </p>
    <p v-if="isLatencyMean(f.signal)" class="banner">
      Mean latency from <code>_sum/_count</code>: histogram buckets are not
      available, so percentiles cannot be shown.
    </p>
    <p v-if="f.attributes?.latency_bound" class="banner">
      Latency at the top histogram bucket: the true value may be higher ({{
        f.attributes.latency_bound
      }}).
    </p>
    <div v-if="f.confidence_reasons.length" class="small">
      Confidence lowered by:
      <ul>
        <li v-for="r in f.confidence_reasons" :key="r.code">{{ r.message }}</li>
      </ul>
    </div>

    <template v-if="primaryEvidence">
      <ChartBox
        :option="evidenceOption(primaryEvidence, f, chartPalette)"
        :label="`Evidence chart for ${f.title}`"
      />
      <DataTable
        :caption="`Evidence values for ${f.title}`"
        :columns="EVIDENCE_COLUMNS"
        :rows="rows"
      />
    </template>
    <p v-else class="banner">
      Evidence for this finding was not stored (see report exclusions).
    </p>

    <div v-if="related.length || coincident.length" class="also">
      <h3 class="label-caps">Also happening</h3>
      <RouterLink v-for="r in related" :key="r.finding_id" :to="findingLink(r.finding_id)" class="other">
        <SeverityChip :severity="r.severity" />
        <span>{{ r.title }} — {{ r.entity.display_name }}</span>
        <span class="muted small">Related: shares identity labels</span>
      </RouterLink>
      <RouterLink v-for="r in coincident" :key="r.finding_id" :to="findingLink(r.finding_id)" class="other dashed">
        <SeverityChip :severity="r.severity" />
        <span>{{ r.title }} — {{ r.entity.display_name }}</span>
        <span class="muted small">Coincides in time (not established as related)</span>
      </RouterLink>
    </div>
    <p v-else class="muted small">
      No related findings: none share an identity label or verified host mapping, and none overlap in time.
    </p>

    <ExplanationPanel
      :report="report"
      :finding-link="findingLink"
      :only="f.finding_id"
    />

    <details class="more">
      <summary>Query, labels and detector</summary>
      <div v-for="e in evidence" :key="e.evidence_id" class="stack">
        <p class="small muted">
          {{ e.series.signal }} · step {{ e.series.step_seconds }} s ·
          {{ formatTime(e.series.start) }} onward
        </p>
        <pre>{{ e.series.query }}</pre>
        <button type="button" @click="copyQuery(e.series.query)">
          Copy query
        </button>
      </div>
      <p v-if="copied" role="status" class="small">Query copied.</p>
      <dl class="labels">
        <template v-for="(v, k) in f.entity.labels" :key="k">
          <dt>{{ k }}</dt>
          <dd>{{ v }}</dd>
        </template>
        <template v-for="(v, k) in f.attributes ?? {}" :key="`a-${k}`">
          <dt>{{ k }}</dt>
          <dd>{{ v }}</dd>
        </template>
      </dl>
      <p class="muted small">
        Detector {{ f.detector }} ({{ f.detector_version }}) ·
        <template v-if="f.baseline_days !== null">
          baseline {{ f.baseline_days }} days{{
            f.baseline_mode === "time_of_day" ? ", time-of-day" : ""
          }}
        </template>
        <template v-else>no baseline (absolute check)</template>
        <template v-if="f.peak_score !== null">
          · robust z {{ f.peak_score.toFixed(1) }}
        </template>
      </p>
    </details>
  </section>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; gap: 14px; }
.panel p { margin: 0; }
.back { font-size: 0.9rem; }
h2 { margin: 0; font-size: 1.4rem; line-height: 1.2; }
.entity { font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-muted); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin: 0; }
.stats div { background: var(--surface); border-radius: var(--radius); padding: 10px 12px; }
.stats dd { margin: 0; font-size: 1.25rem; font-weight: 700; }
.stats .sev-critical { color: var(--sev-critical); }
.stats .sev-high { color: var(--sev-high); }
.stats .sev-medium { color: var(--sev-medium); }
.also { display: flex; flex-direction: column; gap: 6px; }
.also h3 { margin: 0; }
.other {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; padding: 10px 12px;
  border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); text-decoration: none; font-size: 0.9rem;
}
.other:hover { background: var(--surface); }
.other.dashed { border-style: dashed; }
.more { border-top: 1px solid var(--subtle); padding-top: 12px; }
.more summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; }
.more > * + * { margin-top: 10px; }
.labels {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px var(--space);
  font-size: 0.85rem;
}
.labels dt {
  color: var(--text-muted);
}
.labels dd {
  margin: 0;
  font-family: var(--font-mono);
  word-break: break-all;
}
.small {
  font-size: 0.85rem;
}
h2:focus {
  outline: none;
}
h2:focus-visible {
  outline: 2px solid var(--focus);
}
</style>
