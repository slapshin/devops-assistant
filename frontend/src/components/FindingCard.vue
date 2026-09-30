<script setup lang="ts">
import { computed } from "vue";
import type { Finding } from "../api/client";
import { formatDuration, formatTime, formatValue, utcTooltip } from "../lib/format";
import ConfidenceBadge from "./ConfidenceBadge.vue";
import SeverityChip from "./SeverityChip.vue";

const props = defineProps<{ finding: Finding; to: string }>();
const f = computed(() => props.finding);
const expected = computed(() => {
  const e = f.value.expected;
  if (e) return `expected ${formatValue(e.median, e.unit)} (${formatValue(e.lower, e.unit)}–${formatValue(e.upper, e.unit)})`;
  if (f.value.threshold !== null) return `heuristic ${formatValue(f.value.threshold, f.value.observed.unit)}`;
  return "";
});
</script>

<template>
  <article class="card finding">
    <div class="row">
      <SeverityChip :severity="f.severity" />
      <RouterLink :to="to" class="title">{{ f.title }}</RouterLink>
    </div>
    <div class="entity">{{ f.entity.display_name }}</div>
    <div class="row small">
      <span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span>
      <span>· {{ formatDuration(f.duration_seconds) }}</span>
      <span>· {{ f.state === "ongoing" ? "Ongoing" : "Resolved" }}</span>
      <span v-if="f.recurrence !== 'new'">· {{ f.recurrence === "recurring" ? "Recurring" : "Seen before" }} ({{ f.prior_episode_days }} of 13 earlier days)</span>
    </div>
    <div class="small">
      Observed <strong>{{ formatValue(f.observed.value, f.observed.unit) }}</strong>
      <span v-if="expected" class="muted"> vs {{ expected }}</span>
      <span v-if="f.signal === 'latency_mean'" class="muted"> · mean latency (no histogram)</span>
    </div>
    <ConfidenceBadge :confidence="f.confidence" />
  </article>
</template>

<style scoped>
.finding { display: flex; flex-direction: column; gap: 4px; }
.title { font-weight: 600; }
.entity { font-family: var(--font-mono); font-size: 0.85rem; }
.small { font-size: 0.85rem; }
</style>
