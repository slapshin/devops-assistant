<script setup lang="ts">
/** Decorative mini chart of a finding's evidence: usual band, episode span, heuristic line, observed series with gaps. */
import { computed } from "vue";
import type { Evidence, Finding } from "../api/client";
import { timestamps } from "../lib/charts";
import { formatTime, formatValue } from "../lib/format";

/** SVG user units; the svg scales with its width and keeps this aspect ratio. */
const WIDTH = 400;
const HEIGHT = 134;
const PLOT = { left: 44, right: 392, top: 10, bottom: 110 };
const X_LABEL_Y = 128;
/** Keeps very short episodes visible. */
const MIN_EPISODE_WIDTH = 2;
/** x-axis labels sit at the centres of four equal slices. */
const X_TICK_FRACTIONS = [0.125, 0.375, 0.625, 0.875];
/** The y-axis top is rounded up to a multiple of half its order of magnitude: 0.963 → 1, 0.12 → 0.15. */
const NICE_STEPS_PER_DECADE = 2;

const props = defineProps<{ evidence: Evidence; finding: Finding }>();

interface Scale {
  x: (index: number) => number;
  y: (value: number) => number;
}

function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const decade = 10 ** Math.floor(Math.log10(value));
  return (Math.ceil((value / decade) * NICE_STEPS_PER_DECADE) / NICE_STEPS_PER_DECADE) * decade;
}

/** "100.0 %" → "100 %": axis labels drop a trailing ".0". */
const axisText = (value: number, unit: string) => formatValue(value, unit).replace(/\.0(?=\s|$|\/)/, "");

const point = (scale: Scale, index: number, value: number) => `${scale.x(index).toFixed(1)},${scale.y(value).toFixed(1)}`;

/** Runs of consecutive non-null points; gaps lift the pen instead of interpolating. */
function segments(values: (number | null)[]): { index: number; value: number }[][] {
  const runs: { index: number; value: number }[][] = [];
  let current: { index: number; value: number }[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) runs.push(current);
      current = [];
      return;
    }
    current.push({ index, value });
  });
  if (current.length) runs.push(current);
  return runs;
}

function bandPath(evidence: Evidence, scale: Scale): string {
  const { lower, upper } = evidence;
  if (!lower || !upper) return "";

  const top: string[] = [];
  const bottom: string[] = [];
  upper.forEach((high, i) => {
    const low = lower[i];
    if (high === null || low === null || low === undefined) return;
    top.push(point(scale, i, high));
    bottom.unshift(point(scale, i, low));
  });
  return top.length > 1 ? `M${top.join("L")}L${bottom.join("L")}Z` : "";
}

function episodeRect(categories: string[], finding: Finding, scale: Scale) {
  const startIndex = categories.findIndex((t) => t >= finding.start);
  if (startIndex < 0) return null;

  const endIndex = categories.findIndex((t) => t >= finding.end);
  const lastIndex = endIndex >= 0 ? endIndex : categories.length - 1;
  return { x: scale.x(startIndex), w: Math.max(MIN_EPISODE_WIDTH, scale.x(lastIndex) - scale.x(startIndex)) };
}

const shape = computed(() => {
  const { evidence, finding } = props;
  const values = evidence.series.values;
  const unit = evidence.series.unit;
  const numbers = [...values, ...(evidence.upper ?? []), ...(evidence.lower ?? []), evidence.threshold ?? null].filter(
    (v): v is number => typeof v === "number",
  );
  if (values.length < 2 || numbers.length === 0) return null;

  const min = Math.min(0, ...numbers);
  const max = niceCeil(Math.max(...numbers));
  const range = max - min || 1;
  const scale: Scale = {
    x: (index) => PLOT.left + (index / (values.length - 1)) * (PLOT.right - PLOT.left),
    y: (value) => PLOT.bottom - ((value - min) / range) * (PLOT.bottom - PLOT.top),
  };
  const categories = timestamps(evidence);
  const runs = segments(values);
  const threshold = evidence.threshold;

  return {
    lines: runs.map((run) => run.map((p) => point(scale, p.index, p.value)).join(" ")),
    areas: runs
      .filter((run) => run.length > 1)
      .map((run) => {
        const first = run[0]!;
        const last = run[run.length - 1]!;
        const base = scale.y(Math.max(min, 0));
        return `${run.map((p) => point(scale, p.index, p.value)).join(" ")} ${scale.x(last.index).toFixed(1)},${base} ${scale.x(first.index).toFixed(1)},${base}`;
      }),
    band: bandPath(evidence, scale),
    episode: episodeRect(categories, finding, scale),
    threshold: threshold !== null && threshold !== undefined ? scale.y(threshold) : null,
    yTicks: [max, (max + min) / 2, min].map((v) => ({ y: scale.y(v), text: axisText(v, unit) })),
    xTicks: X_TICK_FRACTIONS.map((fraction) => {
      const index = Math.round(fraction * (values.length - 1));
      return { x: scale.x(index), text: categories[index] ? formatTime(categories[index], false, false) : "" };
    }),
  };
});
</script>

<template>
  <svg v-if="shape" :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" width="100%" class="mini" :class="`tone-${finding.severity}`" aria-hidden="true" focusable="false">
    <line v-for="(t, i) in shape.yTicks" :key="`g${i}`" :x1="PLOT.left" :x2="PLOT.right" :y1="t.y" :y2="t.y" :class="i === shape.yTicks.length - 1 ? 'base' : 'grid'" />
    <text v-for="(t, i) in shape.yTicks" :key="`y${i}`" :x="PLOT.left - 6" :y="t.y + 4" text-anchor="end" class="axis">{{ t.text }}</text>
    <path v-if="shape.band" :d="shape.band" class="band" />
    <rect v-if="shape.episode" :x="shape.episode.x" :y="PLOT.top" :width="shape.episode.w" :height="PLOT.bottom - PLOT.top" class="episode" />
    <line v-if="shape.threshold !== null" :x1="PLOT.left" :x2="PLOT.right" :y1="shape.threshold" :y2="shape.threshold" class="threshold" />
    <polygon v-for="(a, i) in shape.areas" :key="`a${i}`" :points="a" class="area" />
    <polyline v-for="(l, i) in shape.lines" :key="`l${i}`" :points="l" class="line" />
    <text v-for="(t, i) in shape.xTicks" :key="`x${i}`" :x="t.x" :y="X_LABEL_Y" text-anchor="middle" class="axis">{{ t.text }}</text>
  </svg>
</template>

<style scoped>
.mini { display: block; font-family: var(--font-sans); --sev: var(--crit); }
.mini.tone-high { --sev: var(--high); }
.mini.tone-medium { --sev: var(--med); }
.mini.tone-low { --sev: var(--low); }
.grid { stroke: var(--grid); }
.base { stroke: var(--border2); }
.axis { fill: var(--muted); font-size: 10px; }
.band { fill: var(--info); fill-opacity: 0.12; }
.episode { fill: var(--sev); fill-opacity: 0.12; }
.threshold { stroke: var(--sev); stroke-dasharray: 4 3; }
.area { fill: var(--s1); fill-opacity: 0.12; }
.line { fill: none; stroke: var(--s1); stroke-width: 1.5; stroke-linejoin: round; }
</style>
