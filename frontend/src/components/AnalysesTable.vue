<script setup lang="ts">
import type { AnalysisJob } from "../api/client";
import { JOB_STATES, formatTime, utcTooltip } from "../lib/format";
import SeverityCounts from "./SeverityCounts.vue";

defineProps<{ jobs: AnalysisJob[] }>();

const link = (job: AnalysisJob) => (job.report_available ? `/reports/${job.analysis_id}` : `/analyses/${job.analysis_id}`);
</script>

<template>
  <div class="table-wrap">
    <table class="table">
      <thead>
        <tr><th scope="col">Window end</th><th scope="col">State</th><th scope="col">Findings</th><th scope="col">AI</th></tr>
      </thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.analysis_id">
          <td>
            <RouterLink :to="link(job)" :title="utcTooltip(job.end_time)">{{ formatTime(job.end_time) }}</RouterLink>
          </td>
          <td>{{ JOB_STATES[job.state] ?? job.state }}<span v-if="job.error" class="muted"> · {{ job.error.code }}</span></td>
          <td><SeverityCounts :counts="job.finding_counts" /></td>
          <td>{{ job.explanation_status.replaceAll("_", " ") }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
