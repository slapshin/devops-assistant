<script setup lang="ts">
/** When each finding happened in the latest-day window, one lane per finding. */
import { computed } from "vue";
import type { Finding } from "../api/client";
import { timeOverlaps } from "../lib/findings";
import { FAMILY_LABELS, formatDuration, formatTime } from "../lib/format";

const MAX_LANES = 8;
const TICK_INTERVALS = 6;
/** Percent of the track; keeps a sub-minute episode visible. */
const MIN_BAR_WIDTH_PERCENT = 0.6;
/** Percent gap between a bar and its time label. */
const LABEL_GAP_PERCENT = 0.8;
/** Bars ending past this point get their label on the left when there is more room there. */
const LABEL_FLIP_PERCENT = 55;

const props = defineProps<{ findings: Finding[]; start: string; end: string; link: (id: string) => string }>();

const windowStartMs = computed(() => new Date(props.start).getTime());
const windowSpanMs = computed(() => new Date(props.end).getTime() - windowStartMs.value || 1);

const shownFindings = computed(() => props.findings.slice(0, MAX_LANES).sort((a, b) => a.start.localeCompare(b.start)));
const lanes = computed(() => shownFindings.value.map(toLane));
const ticks = computed(() => Array.from({ length: TICK_INTERVALS + 1 }, (_, i) => toTick(i)));
const overlaps = computed(() => timeOverlaps(shownFindings.value));

function positionPercent(iso: string): number {
  const offset = (new Date(iso).getTime() - windowStartMs.value) / windowSpanMs.value;
  return Math.min(100, Math.max(0, offset * 100));
}

function toLane(f: Finding) {
  const left = positionPercent(f.start);
  const width = Math.max(MIN_BAR_WIDTH_PERCENT, positionPercent(f.end) - left);
  const right = left + width;
  const labelOnLeft = right > LABEL_FLIP_PERCENT && left > 100 - right;
  return {
    f,
    bar: { left: `${left}%`, width: `${width}%` },
    label: labelOnLeft ? { right: `${100 - left + LABEL_GAP_PERCENT}%` } : { left: `${right + LABEL_GAP_PERCENT}%` },
    text: `${formatTime(f.start, false)} · ${formatDuration(f.duration_seconds)}`,
  };
}

/** Edge ticks are aligned inward so their labels stay inside the axis. */
function tickEdge(index: number): string {
  if (index === 0) return "first";
  if (index === TICK_INTERVALS) return "last";
  return "";
}

function toTick(index: number) {
  const time = new Date(windowStartMs.value + (windowSpanMs.value * index) / TICK_INTERVALS).toISOString();
  return {
    left: `${(index / TICK_INTERVALS) * 100}%`,
    text: index === TICK_INTERVALS ? "end" : formatTime(time, false),
    edge: tickEdge(index),
  };
}
</script>

<template>
  <section class="card timeline" aria-labelledby="timeline-title">
    <div class="head">
      <h2 id="timeline-title">When it happened</h2>
      <span class="muted small">Latest 24 h</span>
    </div>
    <ul class="lanes">
      <li v-for="l in lanes" :key="l.f.finding_id" class="lane">
        <RouterLink :to="link(l.f.finding_id)" class="name">
          <strong>{{ FAMILY_LABELS[l.f.family] ?? l.f.family }}</strong>
          <span class="mono">{{ l.f.entity.display_name }}</span>
        </RouterLink>
        <div class="track">
          <span class="bar" :class="`sev-${l.f.severity}`" :style="l.bar" />
          <span class="when" :class="`sev-${l.f.severity}`" :style="l.label">{{ l.text }}</span>
        </div>
      </li>
    </ul>
    <div class="axis" aria-hidden="true">
      <span v-for="t in ticks" :key="t.left" :class="t.edge" :style="{ left: t.left }">{{ t.text }}</span>
    </div>
    <p v-if="findings.length > MAX_LANES" class="muted small">Showing the {{ MAX_LANES }} most severe of {{ findings.length }} findings.</p>
    <p v-for="[a, b] in overlaps" :key="`${a.finding_id}-${b.finding_id}`" class="muted small">
      {{ a.title }} and {{ b.title }} coincide in time (not established as related).
    </p>
  </section>
</template>

<style scoped>
.timeline { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.head h2 { margin: 0; }
.lanes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.lane { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: center; }
.name { display: flex; flex-direction: column; color: var(--text); text-decoration: none; font-size: 0.9rem; min-width: 0; }
.name .mono { font-size: 0.75rem; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.name:hover strong { text-decoration: underline; }
.track { position: relative; overflow: hidden; height: 30px; background: var(--surface); border-radius: 4px; }
.bar { position: absolute; top: 4px; bottom: 4px; border-radius: 3px; }
.when { position: absolute; top: 6px; font-size: 0.75rem; white-space: nowrap; }
.bar.sev-critical { background: var(--bar-critical); }
.bar.sev-high { background: var(--bar-high); }
.bar.sev-medium { background: var(--bar-medium); }
.bar.sev-low { background: var(--bar-low); }
.when.sev-critical { color: var(--sev-critical); }
.when.sev-high { color: var(--sev-high); }
.when.sev-medium { color: var(--sev-medium); }
.when.sev-low { color: var(--sev-low); }
.axis { position: relative; height: 18px; margin-left: 236px; border-top: 1px solid var(--border); font-size: 0.75rem; color: var(--text-muted); }
.axis span { position: absolute; top: 2px; transform: translateX(-50%); white-space: nowrap; }
.axis .first { transform: none; }
.axis .last { transform: translateX(-100%); }
.small { font-size: 0.8rem; margin: 0; }
@media (max-width: 700px) {
  .lane { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .axis { margin-left: 0; }
  .axis span:nth-child(even) { display: none; }
}
</style>
