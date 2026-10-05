<script setup lang="ts">
import type { AnalysisReport } from "../api/client";
import { FAMILY_LABELS } from "../lib/format";
import StatusBadge from "./StatusBadge.vue";

defineProps<{ report: AnalysisReport }>();
</script>

<template>
  <section class="panel" aria-labelledby="coverage-title">
    <div class="ph"><h2 id="coverage-title">Coverage and limitations</h2><span class="sub">· missing telemetry is never counted as healthy</span></div>
    <div class="pb">
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr><th scope="col">Signal family</th><th scope="col">Status</th><th scope="col" class="num">Series</th><th scope="col" class="num">Baseline</th><th scope="col">Notes</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in report.coverage" :key="row.family">
              <th scope="row">{{ FAMILY_LABELS[row.family] ?? row.family }}</th>
              <td><StatusBadge :status="row.status" /></td>
              <td class="num">{{ row.evaluated_series }}/{{ row.total_series }}</td>
              <td class="num">{{ row.baseline_days === null || row.baseline_days === undefined ? "—" : `${row.baseline_days} d` }}</td>
              <td class="notes">
                <span v-for="(r, i) in row.reasons" :key="i">{{ r.message }}<br></span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template v-if="report.exclusions.length">
        <h3 class="omitted">Omitted from this report</h3>
        <ul class="exclusions">
          <li v-for="(e, i) in report.exclusions" :key="i"><code>{{ e.code }}</code> {{ e.message }}</li>
        </ul>
      </template>
      <p class="lbl">Thresholds are provisional diagnostic heuristics, not SLOs.</p>
    </div>
  </section>
</template>

<style scoped>
.table tbody th { font-weight: 500; }
.notes { font-size: 12px; color: var(--muted); }
.omitted { margin: 0; }
.exclusions { margin: 0; padding-left: 20px; font-size: 13px; }
</style>
