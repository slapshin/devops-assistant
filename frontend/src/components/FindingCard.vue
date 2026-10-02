<script setup lang="ts">
import { computed } from "vue";
import type { Evidence, Finding } from "../api/client";
import { recurrenceText, usualText } from "../lib/findings";
import { formatDuration, formatTime, formatValue, isLatencyMean, utcTooltip } from "../lib/format";
import ConfidenceBadge from "./ConfidenceBadge.vue";
import SeverityChip from "./SeverityChip.vue";
import EvidenceSparkline from "./EvidenceSparkline.vue";

const props = defineProps<{ finding: Finding; to: string; evidence?: Evidence | null }>();

const f = computed(() => props.finding);
</script>

<template>
  <article class="card finding" :class="`sev-${f.severity}`">
    <div class="row">
      <SeverityChip :severity="f.severity" />
      <span class="tag" :class="f.recurrence === 'new' ? 'new' : 'seen'">{{ recurrenceText(f) }}</span>
    </div>
    <RouterLink :to="to" class="title">{{ f.title }}</RouterLink>
    <div class="entity">{{ f.entity.display_name }}</div>
    <div class="value">
      <strong>{{ formatValue(f.observed.value, f.observed.unit) }}</strong>
      <span class="muted">{{ usualText(f) }}</span>
    </div>
    <span v-if="isLatencyMean(f.signal)" class="muted small">Mean latency (no histogram)</span>
    <EvidenceSparkline v-if="evidence" :evidence="evidence" :finding="f" />
    <div class="row small muted">
      <span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span>
      <span>· {{ formatDuration(f.duration_seconds) }}</span>
      <span>· {{ f.state === "ongoing" ? "Ongoing" : "Resolved" }}</span>
      <ConfidenceBadge :confidence="f.confidence" />
    </div>
  </article>
</template>

<style scoped>
.finding { display: flex; flex-direction: column; gap: 8px; border-top-width: 4px; }
.sev-critical { border-top-color: var(--bar-critical); }
.sev-high { border-top-color: var(--bar-high); }
.sev-medium { border-top-color: var(--bar-medium); }
.sev-low { border-top-color: var(--bar-low); }
.title { font-weight: 600; font-size: 1rem; color: var(--text); text-decoration: none; }
.title:hover { text-decoration: underline; }
.entity { font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-muted); }
.value { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.value strong { font-size: 1.9rem; line-height: 1.1; }
.sev-critical .value strong { color: var(--sev-critical); }
.sev-high .value strong { color: var(--sev-high); }
.sev-medium .value strong { color: var(--sev-medium); }
.small { font-size: 0.8rem; gap: 4px; }
</style>
