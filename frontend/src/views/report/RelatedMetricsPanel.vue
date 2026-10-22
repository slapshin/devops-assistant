<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Finding, FindingDetail } from "../../api/client";
import ChartBox from "../../components/ChartBox.vue";
import DataTable from "../../components/DataTable.vue";
import SourceIcon from "../../components/projects/SourceIcon.vue";
import { formatTime, timezone, utcTooltip } from "../../lib/format";
import { sourceKindLabel } from "../../lib/projects";
import {
  ASSOCIATION_LABELS,
  RELATED_DEFAULT_ROWS,
  evaluationCounts,
  evaluationMessage,
  isVerified,
  pairRows,
  qualityText,
  relatedFor,
  relatedPairOption,
  seriesTitle,
  similarityText,
  stepMinutes,
  timingText,
  type RelatedView,
} from "../../lib/related";
import { chartPalette } from "../../lib/theme";
import { useReportContext } from "./context";

const props = defineProps<{ finding: Finding; detail: FindingDetail }>();

const { report, findingById } = useReportContext();
const showAll = ref(false);
const selectedId = ref<string | null>(null);
const view = ref<RelatedView>("values");

const found = computed(() => relatedFor(props.detail));
const comparisons = computed(() => found.value.comparisons);
const visible = computed(() => (showAll.value ? comparisons.value : comparisons.value.slice(0, RELATED_DEFAULT_ROWS)));
const message = computed(() => evaluationMessage(found.value));
const chartById = computed(() => new Map((found.value.related?.charts ?? []).map((c) => [c.chart_id, c])));
const selected = computed(() => comparisons.value.find((c) => c.comparison_id === selectedId.value) ?? null);
const anchorChart = computed(() => (selected.value ? chartById.value.get(selected.value.anchor.chart_id) ?? null : null));
const candidateChart = computed(() => (selected.value ? chartById.value.get(selected.value.candidate.chart_id) ?? null : null));
const columns = computed(() =>
  selected.value
    ? ["Time", `${selected.value.anchor.label}`, "z", `${selected.value.candidate.label}`, "z"]
    : [],
);
const rows = computed(() =>
  selected.value && anchorChart.value && candidateChart.value ? pairRows(anchorChart.value, candidateChart.value) : [],
);
const option = computed(() =>
  selected.value && anchorChart.value && candidateChart.value
    ? relatedPairOption(selected.value, anchorChart.value, candidateChart.value, view.value, chartPalette.value, props.finding)
    : null,
);
const partial = computed(() => report.value.state === "partial");

watch(
  comparisons,
  (list) => {
    selectedId.value = list[0]?.comparison_id ?? null;
  },
  { immediate: true },
);

const findingLink = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;
</script>

<template>
  <section class="panel s24" aria-labelledby="related-title">
    <div class="ph">
      <h2 id="related-title">Related metrics</h2>
      <span v-if="comparisons.length" class="count">{{ comparisons.length }}</span>
      <span class="sub">· what else changed around this finding · similar changes are not causes</span>
    </div>
    <div class="pb">
      <p v-if="message" class="banner" role="status">{{ message }}</p>
      <p v-if="found.evaluation" class="lbl">
        {{ evaluationCounts(found.evaluation) }}
        <template v-if="found.evaluation.window">
          · window <span :title="utcTooltip(found.evaluation.window.start)">{{ formatTime(found.evaluation.window.start) }}</span> –
          <span :title="utcTooltip(found.evaluation.window.end)">{{ formatTime(found.evaluation.window.end) }}</span>
        </template>
      </p>
      <ul v-if="found.evaluation?.reasons?.length && found.evaluation.state === 'evaluated'" class="notes lbl">
        <li v-for="r in found.evaluation.reasons" :key="r.code">{{ r.message }}</li>
      </ul>
      <p v-if="partial && found.related" class="lbl">Some sources failed or were truncated (see the report's exclusions): their metrics could not be compared.</p>

      <template v-if="comparisons.length">
        <div class="table-wrap">
          <table class="gt related">
            <caption class="sr-only">Related metrics of {{ finding.title }}, best first</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col">Resource</th>
                <th scope="col">Similarity</th>
                <th scope="col">Timing</th>
                <th scope="col">Data</th>
                <th scope="col">Finding</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in visible" :key="c.comparison_id" :class="{ sel: c.comparison_id === selectedId }">
                <td class="metric">
                  <button type="button" class="link pick" :aria-pressed="c.comparison_id === selectedId" @click="selectedId = c.comparison_id">
                    <SourceIcon :kind="c.candidate.source" />
                    <span>{{ c.candidate.label }}</span>
                  </button>
                  <span class="mono muted entity">{{ c.candidate.entity.display_name }} · {{ sourceKindLabel(c.candidate.source) }}</span>
                </td>
                <td>
                  <span class="tag" :class="{ unknown: !isVerified(c) }" :title="c.association_basis">{{ ASSOCIATION_LABELS[c.association] }}</span>
                  <span v-if="c.shared_input === 'possible'" class="tag unknown" title="May be computed from the same data: not independent confirmation">possibly same data</span>
                </td>
                <td>{{ similarityText(c) }}</td>
                <td>{{ timingText(c) }}</td>
                <td>
                  {{ qualityText(c) }}
                  <span v-if="c.reasons?.length" class="muted" :title="c.reasons.map((r) => r.message).join('\n')">· {{ c.reasons.length }} note{{ c.reasons.length > 1 ? "s" : "" }}</span>
                </td>
                <td>
                  <RouterLink v-if="c.candidate.finding_id && findingById.get(c.candidate.finding_id)" :to="findingLink(c.candidate.finding_id)">Open</RouterLink>
                  <span v-else class="muted">none</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <button v-if="comparisons.length > RELATED_DEFAULT_ROWS" type="button" class="link" :aria-expanded="showAll" @click="showAll = !showAll">
          {{ showAll ? `Show the first ${RELATED_DEFAULT_ROWS}` : `Show all ${comparisons.length}` }}
        </button>

        <section v-if="selected" class="pair" aria-labelledby="related-pair-title">
          <h3 id="related-pair-title">{{ seriesTitle(selected.anchor) }} and {{ seriesTitle(selected.candidate) }}</h3>
          <p class="selection">{{ selected.selection }}</p>
          <dl class="kv">
            <dt>Resource</dt>
            <dd>{{ ASSOCIATION_LABELS[selected.association] }} — {{ selected.association_basis }}</dd>
            <dt>Similarity</dt>
            <dd>
              {{ similarityText(selected) }}
              <template v-if="selected.method === 'deviation_correlation'"> (deviations from the usual range; not a probability)</template>
              <template v-else> (unusual intervals overlap; no coefficient)</template>
            </dd>
            <dt>Timing</dt>
            <dd>{{ timingText(selected) }} (approximate at {{ stepMinutes(selected) }}-min steps)</dd>
          </dl>
          <ul v-if="selected.reasons?.length" class="notes small">
            <li v-for="r in selected.reasons" :key="r.code">{{ r.message }}</li>
          </ul>
          <div class="tg" role="group" aria-label="Chart scale">
            <button type="button" :aria-pressed="view === 'values'" @click="view = 'values'">Values</button>
            <button type="button" :aria-pressed="view === 'deviation'" @click="view = 'deviation'">Deviation from usual</button>
          </div>
          <template v-if="option && anchorChart && candidateChart">
            <ChartBox :option="option" :label="`${seriesTitle(selected.anchor)} and ${seriesTitle(selected.candidate)} on one time axis`" height="420px" />
            <p class="lbl">
              {{ timezone === "utc" ? "UTC" : "Local time" }} · {{ stepMinutes(selected) }}-min steps · actual timestamps (no shift applied) ·
              {{ qualityText(selected) }}, {{ selected.paired_samples }} steps · gaps are left empty
              <template v-if="view === 'deviation'"> · robust z: distance from the usual median in scaled MADs, clipped at ±10</template>
            </p>
            <DataTable :caption="`Values of ${selected.anchor.label} and ${selected.candidate.label}`" :columns="columns" :rows="rows" />
          </template>
          <p v-else class="banner">The chart data of this comparison was not stored.</p>
          <p v-if="!selected.candidate.finding_id" class="lbl">{{ selected.candidate.label }} has no finding of its own in this window; its stored values are shown here.</p>
        </section>
      </template>
    </div>
  </section>
</template>

<style scoped>
.notes { margin: 0; padding-left: 18px; }
/* Scrolls inside .table-wrap on narrow screens instead of crushing its cells. */
.related { min-width: 860px; }
.related td { height: auto; white-space: normal; vertical-align: top; padding-top: 6px; padding-bottom: 6px; }
.related .metric { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding-top: 6px; padding-bottom: 6px; }
.related tr.sel > td:first-child { box-shadow: inset 3px 0 0 var(--primary); }
.pick { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }
.pick[aria-pressed="true"] { color: var(--strong); }
.entity { font-size: 12px; white-space: normal; overflow-wrap: anywhere; }
.tag.unknown { border-style: dashed; color: var(--muted); }
.pair { display: flex; flex-direction: column; gap: 8px; padding-top: 10px; border-top: 1px solid var(--border); }
.pair h3 { margin: 0; font-size: 15px; font-weight: 500; color: var(--strong); overflow-wrap: anywhere; }
.selection { margin: 0; }
.small { font-size: 12px; }
</style>
