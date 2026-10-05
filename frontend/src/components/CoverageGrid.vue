<script setup lang="ts">
/** Compact coverage: one tile per signal family; the full table follows on the Overview. */
import { computed } from "vue";
import type { SignalCoverage } from "../api/client";
import { FAMILY_LABELS, STATUS_LABELS } from "../lib/format";
import StatusIcon from "./StatusIcon.vue";

/** Problems first, healthy families last. */
const STATUS_DISPLAY_ORDER = ["anomalous", "source_error", "unsupported", "insufficient_data", "not_evaluated", "no_anomaly"];
/** Families that were looked at but could not be judged share the amber "not monitored" fill. */
const TILE_KIND: Record<string, string> = {
  anomalous: "crit",
  source_error: "med",
  unsupported: "med",
  insufficient_data: "med",
  no_anomaly: "ok",
};

const props = defineProps<{ coverage: SignalCoverage[]; baselineDays: number }>();

const sortedCoverage = computed(() =>
  [...props.coverage].sort((a, b) => STATUS_DISPLAY_ORDER.indexOf(a.status) - STATUS_DISPLAY_ORDER.indexOf(b.status)),
);
const evaluated = computed(() => props.coverage.filter((c) => c.status === "anomalous" || c.status === "no_anomaly").length);
</script>

<template>
  <section class="panel" aria-labelledby="coverage-grid-title">
    <div class="ph">
      <h2 id="coverage-grid-title">Signal families</h2>
      <span class="sub">
        · {{ evaluated }} of {{ coverage.length }} evaluated<template v-if="baselineDays"> · {{ baselineDays }}-day baseline</template>
        · thresholds are diagnostic heuristics, not SLOs
      </span>
    </div>
    <div class="pb">
      <ul class="tiles">
        <li v-for="row in sortedCoverage" :key="row.family" :class="`k-${TILE_KIND[row.status] ?? 'none'}`">
          <StatusIcon :status="row.status" />
          {{ FAMILY_LABELS[row.family] ?? row.family }}
          <span class="sr-only">: {{ STATUS_LABELS[row.status]?.label ?? row.status }}</span>
        </li>
      </ul>
      <div class="legend">
        <span><span class="sw box k-crit" />Anomalous</span>
        <span><span class="sw box k-med" />Not evaluated (unsupported, insufficient data or source error)</span>
        <span><span class="sw box k-ok" />No anomaly</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 4px; }
.tiles li { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 12px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500; }
.k-crit { background: var(--f-crit); color: #ffffff; }
.k-med { background: var(--f-med); color: var(--on-med); }
.k-ok { background: var(--f-ok); color: #ffffff; }
.k-none { background: var(--f-none); color: var(--text); }
</style>
