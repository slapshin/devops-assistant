<script setup lang="ts">
/** One finding as a dashboard panel that opens its evidence: peak against the usual range, mini chart, time and state. */
import { computed } from "vue";
import type { Evidence, Finding } from "../api/client";
import { recurrenceText, usualText } from "../lib/findings";
import { formatDuration, formatTime, formatValue, isLatencyMean, utcTooltip } from "../lib/format";
import EvidenceSparkline from "./EvidenceSparkline.vue";
import SeverityChip from "./SeverityChip.vue";

const props = defineProps<{ finding: Finding; to: string; evidence?: Evidence | null }>();

const f = computed(() => props.finding);
const threshold = computed(() => props.evidence?.threshold ?? f.value.threshold);
</script>

<template>
  <RouterLink :to="to" class="panel finding">
    <div class="ph">
      <SeverityChip :severity="f.severity" />
      <h3>{{ f.title }}</h3>
      <span class="tag" :class="{ new: f.recurrence === 'new' }">{{ recurrenceText(f) }}</span>
    </div>
    <div class="pb">
      <div class="value">
        <strong :class="`v-${f.severity}`">{{ formatValue(f.observed.value, f.observed.unit) }}</strong>
        <span class="lbl">peak · {{ usualText(f) || "no usual range" }}</span>
      </div>
      <span v-if="isLatencyMean(f.signal)" class="lbl">Mean latency (no histogram)</span>
      <EvidenceSparkline v-if="evidence" :evidence="evidence" :finding="f" />
      <div class="legend">
        <span><span class="sw" style="background: var(--s1)" /><span class="mono">{{ f.entity.display_name }}</span></span>
        <span v-if="threshold !== null && threshold !== undefined">
          <span class="sw current" :class="`v-${f.severity}`" />
          {{ formatValue(threshold, f.observed.unit) }} heuristic
        </span>
        <span v-else-if="evidence?.lower"><span class="sw box band" />usual range</span>
      </div>
      <div class="lbl">
        <span :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</span>
        · {{ formatDuration(f.duration_seconds) }} · {{ f.state === "ongoing" ? "ongoing" : "resolved" }} · confidence {{ f.confidence }}
      </div>
    </div>
  </RouterLink>
</template>

<style scoped>
.finding { text-decoration: none; color: var(--text); }
.finding:hover { border-color: var(--border2); background: var(--hover); color: var(--text); }
.finding h3 { flex: 1 1 auto; min-width: 0; white-space: nowrap; }
.finding .ph { padding-top: 6px; }
.finding .pb { gap: 6px; }
.value { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.value strong { font-size: 26px; font-weight: 600; line-height: 1.15; }
.band { background: var(--info); opacity: 0.35; }
.current { background: currentColor; }
</style>
