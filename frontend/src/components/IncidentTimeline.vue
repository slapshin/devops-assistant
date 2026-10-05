<script setup lang="ts">
/** When each finding happened in the latest-day window, one lane per finding. */
import { computed } from "vue";
import type { Finding } from "../api/client";
import { timeOverlaps } from "../lib/findings";
import { SEVERITIES, capitalize, formatDuration, formatTime } from "../lib/format";

const MAX_LANES = 8;
const TICK_INTERVALS = 6;
/** Percent of the track; keeps a sub-minute episode visible. */
const MIN_BAR_WIDTH_PERCENT = 0.6;
/** Percent gap between a bar and its time label. */
const LABEL_GAP_PERCENT = 0.8;
/** Bars at least this wide (percent of the track) carry their label inside. */
const LABEL_INSIDE_PERCENT = 9;
/** Bars ending past this point get their label on the left when there is more room there. */
const LABEL_FLIP_PERCENT = 55;

const props = defineProps<{ findings: Finding[]; start: string; end: string; link: (id: string) => string }>();

const windowStartMs = computed(() => new Date(props.start).getTime());
const windowSpanMs = computed(() => new Date(props.end).getTime() - windowStartMs.value || 1);

const shownFindings = computed(() => props.findings.slice(0, MAX_LANES).sort((a, b) => a.start.localeCompare(b.start)));
const lanes = computed(() => shownFindings.value.map(toLane));
const ticks = computed(() => Array.from({ length: TICK_INTERVALS + 1 }, (_, i) => toTick(i)));
const overlaps = computed(() => timeOverlaps(shownFindings.value));
const legend = computed(() => SEVERITIES.filter((s) => props.findings.some((f) => f.severity === s)));

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
    inside: width >= LABEL_INSIDE_PERCENT,
    label: labelOnLeft ? { right: `${100 - left + LABEL_GAP_PERCENT}%` } : { left: `${right + LABEL_GAP_PERCENT}%` },
    text: `${formatTime(f.start, false, false)} · ${formatDuration(f.duration_seconds)}`,
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
    text: index === TICK_INTERVALS ? "end" : formatTime(time, false, false),
    edge: tickEdge(index),
  };
}
</script>

<template>
  <section class="panel" aria-labelledby="timeline-title">
    <div class="ph">
      <h2 id="timeline-title">State timeline</h2>
      <span class="sub">· latest 24 h · overlap means episodes coincide in time; the detector hasn't established that they're related</span>
    </div>
    <div class="pb">
      <ul class="lanes">
        <li v-for="l in lanes" :key="l.f.finding_id" class="lane">
          <RouterLink :to="link(l.f.finding_id)" class="name">
            <span class="t">{{ l.f.title }}</span>
            <span class="mono e">{{ l.f.entity.display_name }}</span>
          </RouterLink>
          <div class="track">
            <span class="bar" :class="`sev-${l.f.severity}`" :style="l.bar"><span v-if="l.inside" class="in">{{ l.text }}</span></span>
            <span v-if="!l.inside" class="when" :style="l.label">{{ l.text }}</span>
          </div>
        </li>
      </ul>
      <div class="axis" aria-hidden="true">
        <span v-for="t in ticks" :key="t.left" :class="t.edge" :style="{ left: t.left }">{{ t.text }}</span>
      </div>
      <div class="legend">
        <span><span class="sw box none" />No episode</span>
        <span v-for="s in legend" :key="s"><span class="sw box" :class="`sev-${s}`" />{{ capitalize(s) }}</span>
      </div>
      <p v-if="findings.length > MAX_LANES" class="lbl">Showing the {{ MAX_LANES }} most severe of {{ findings.length }} findings.</p>
      <p v-for="[a, b] in overlaps" :key="`${a.finding_id}-${b.finding_id}`" class="lbl">
        {{ a.title }} and {{ b.title }} coincide in time (not established as related).
      </p>
    </div>
  </section>
</template>

<style scoped>
.lanes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.lane { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 12px; align-items: center; }
.name { display: flex; flex-direction: column; align-items: flex-end; text-align: right; color: var(--strong); text-decoration: none; font-size: 13px; min-width: 0; }
.name > span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.name .e { font-size: 11px; color: var(--muted); }
.name:hover .t { text-decoration: underline; }
.track { position: relative; overflow: hidden; height: 28px; background: var(--f-none); border-radius: var(--radius-sm); }
.bar { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; overflow: hidden; }
.in { padding-left: 6px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.when { position: absolute; top: 6px; font-size: 12px; white-space: nowrap; color: var(--text); }
.sw.none { background: var(--f-none); }
.axis { position: relative; height: 18px; margin-left: 232px; font-size: 11px; color: var(--muted); }
.axis span { position: absolute; top: 2px; transform: translateX(-50%); white-space: nowrap; }
.axis .first { transform: none; }
.axis .last { transform: translateX(-100%); }
@media (max-width: 700px) {
  .lane { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .name { align-items: flex-start; text-align: left; }
  .axis { margin-left: 0; }
  .axis span:nth-child(even) { display: none; }
}
</style>
