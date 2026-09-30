<script setup lang="ts">
import type { AnalysisReport } from "../api/client";
import { FAMILY_LABELS } from "../lib/format";
import StatusBadge from "./StatusBadge.vue";

defineProps<{ report: AnalysisReport }>();
</script>

<template>
  <section id="coverage" aria-labelledby="coverage-title">
    <h2 id="coverage-title">Coverage and limitations</h2>
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr><th scope="col">Signal family</th><th scope="col">Status</th><th scope="col">Series</th><th scope="col">Baseline</th><th scope="col">Notes</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in report.coverage" :key="row.family">
            <th scope="row">{{ FAMILY_LABELS[row.family] ?? row.family }}</th>
            <td><StatusBadge :status="row.status" /></td>
            <td>{{ row.evaluated_series }}/{{ row.total_series }}</td>
            <td>{{ row.baseline_days === null || row.baseline_days === undefined ? "—" : `${row.baseline_days} d` }}</td>
            <td class="notes">
              <span v-for="(r, i) in row.reasons" :key="i">{{ r.message }}<br></span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <template v-if="report.exclusions.length">
      <h3>Omitted from this report</h3>
      <ul>
        <li v-for="(e, i) in report.exclusions" :key="i"><code>{{ e.code }}</code> {{ e.message }}</li>
      </ul>
    </template>
    <p class="muted small">Thresholds are provisional diagnostic heuristics, not SLOs. Missing telemetry is never counted as healthy.</p>
  </section>
</template>

<style scoped>
.notes { font-size: 0.85rem; color: var(--text-muted); }
.small { font-size: 0.8rem; }
</style>
