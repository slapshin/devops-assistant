<script setup lang="ts">
/** Compact coverage: one tile per signal family; the full table stays below on the Overview. */
import { computed } from "vue";
import type { SignalCoverage } from "../api/client";
import { FAMILY_LABELS, STATUS_LABELS } from "../lib/format";

/** Problems first, healthy families last. */
const STATUS_DISPLAY_ORDER = ["anomalous", "source_error", "unsupported", "insufficient_data", "not_evaluated", "no_anomaly"];

const props = defineProps<{ coverage: SignalCoverage[] }>();

const sortedCoverage = computed(() =>
  [...props.coverage].sort((a, b) => STATUS_DISPLAY_ORDER.indexOf(a.status) - STATUS_DISPLAY_ORDER.indexOf(b.status)),
);
</script>

<template>
  <section class="card grid-card" aria-labelledby="coverage-grid-title">
    <div class="head">
      <h2 id="coverage-grid-title">Coverage</h2>
      <a href="#coverage">Full table</a>
    </div>
    <ul class="tiles">
      <li v-for="row in sortedCoverage" :key="row.family" :class="`st-${row.status}`">
        <span aria-hidden="true">{{ STATUS_LABELS[row.status]?.icon ?? "?" }}</span>
        {{ FAMILY_LABELS[row.family] ?? row.family }}
        <span class="sr-only">: {{ STATUS_LABELS[row.status]?.label ?? row.status }}</span>
      </li>
    </ul>
    <p class="muted small">
      Red: anomalous · amber: not evaluated (unsupported, insufficient data or source error) · green: no anomaly.
      Thresholds are diagnostic heuristics, not SLOs.
    </p>
  </section>
</template>

<style scoped>
.grid-card { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; justify-content: space-between; align-items: baseline; }
.head h2 { margin: 0; }
.tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; font-size: 0.9rem; }
.tiles li { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: var(--radius); background: var(--surface); }
.tiles .st-anomalous { background: var(--sev-critical-bg); color: var(--sev-critical); font-weight: 600; }
.tiles .st-unsupported, .tiles .st-insufficient_data, .tiles .st-source_error { background: var(--sev-medium-bg); color: var(--sev-medium); font-weight: 600; }
.st-no_anomaly { color: var(--status-ok); }
.st-not_evaluated { color: var(--text-muted); }
.small { font-size: 0.8rem; margin: 0; }
</style>
