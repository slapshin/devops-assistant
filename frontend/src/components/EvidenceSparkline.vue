<script setup lang="ts">
/** Decorative mini chart of a finding's evidence: expected band, heuristic line, observed series with gaps. */
import { computed } from "vue";
import type { Evidence, Finding } from "../api/client";
import { timestamps } from "../lib/charts";

/** SVG user units; the svg stretches to its CSS box (preserveAspectRatio="none"). */
const WIDTH = 300;
const HEIGHT = 56;
const PADDING = 4;
/** Keeps very short episodes visible. */
const MIN_EPISODE_WIDTH = 2;

const props = defineProps<{ evidence: Evidence; finding: Finding }>();

interface Scale {
  x: (index: number) => number;
  y: (value: number) => number;
}

const point = (scale: Scale, index: number, value: number) => `${scale.x(index).toFixed(1)},${scale.y(value).toFixed(1)}`;

function makeScale(evidence: Evidence): Scale | null {
  const pointCount = evidence.series.values.length;
  const numbers = [...evidence.series.values, ...(evidence.lower ?? []), ...(evidence.upper ?? []), evidence.threshold ?? null].filter(
    (v): v is number => typeof v === "number",
  );
  if (pointCount < 2 || numbers.length === 0) return null;

  const min = Math.min(...numbers);
  const range = Math.max(...numbers) - min || 1;
  return {
    x: (index) => (index / (pointCount - 1)) * WIDTH,
    y: (value) => PADDING + (1 - (value - min) / range) * (HEIGHT - 2 * PADDING),
  };
}

/** Gaps lift the pen, so missing data shows as breaks rather than interpolated lines. */
function observedPath(values: (number | null)[], scale: Scale): string {
  let path = "";
  let penDown = false;
  values.forEach((value, i) => {
    if (value === null) {
      penDown = false;
      return;
    }
    path += `${penDown ? "L" : "M"}${point(scale, i, value)}`;
    penDown = true;
  });
  return path;
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

function episodeRect(evidence: Evidence, finding: Finding, scale: Scale) {
  const categories = timestamps(evidence);
  const startIndex = categories.findIndex((t) => t >= finding.start);
  if (startIndex < 0) return null;

  const endIndex = categories.findIndex((t) => t >= finding.end);
  const lastIndex = endIndex >= 0 ? endIndex : categories.length - 1;
  return { x: scale.x(startIndex), w: Math.max(MIN_EPISODE_WIDTH, scale.x(lastIndex) - scale.x(startIndex)) };
}

const shape = computed(() => {
  const { evidence, finding } = props;
  const scale = makeScale(evidence);
  if (!scale) return null;

  const threshold = evidence.threshold;
  return {
    line: observedPath(evidence.series.values, scale),
    band: bandPath(evidence, scale),
    episode: episodeRect(evidence, finding, scale),
    threshold: threshold !== null && threshold !== undefined ? scale.y(threshold) : null,
  };
});
</script>

<template>
  <svg v-if="shape" :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" preserveAspectRatio="none" class="spark" aria-hidden="true" focusable="false">
    <rect v-if="shape.episode" :x="shape.episode.x" y="0" :width="shape.episode.w" :height="HEIGHT" class="episode" />
    <path v-if="shape.band" :d="shape.band" class="band" />
    <line v-if="shape.threshold !== null" x1="0" :x2="WIDTH" :y1="shape.threshold" :y2="shape.threshold" class="threshold" />
    <path :d="shape.line" class="observed" vector-effect="non-scaling-stroke" />
  </svg>
</template>

<style scoped>
.spark { display: block; width: 100%; height: 56px; }
.episode { fill: var(--sev-critical-bg); }
.band { fill: var(--info-bg); }
.threshold { stroke: var(--sev-critical); stroke-dasharray: 3 3; stroke-width: 1; vector-effect: non-scaling-stroke; }
.observed { fill: none; stroke: var(--text); stroke-width: 1.6; stroke-linejoin: round; }
</style>
