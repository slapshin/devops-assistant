<script setup lang="ts">
/** Line of one series over time with gaps, and an optional threshold line (custom rule previews). */
import { computed } from "vue";

const WIDTH = 160;
const HEIGHT = 32;
const PAD = 2;

const props = defineProps<{ values: (number | null)[]; threshold?: number | null; label: string }>();

const scale = computed(() => {
  const known = props.values.filter((v): v is number => v !== null);
  const all = props.threshold === null || props.threshold === undefined ? known : [...known, props.threshold];
  const min = Math.min(0, ...all);
  const max = Math.max(...all, min + 1e-9);
  const n = Math.max(1, props.values.length - 1);
  return {
    x: (i: number) => (i / n) * WIDTH,
    y: (v: number) => HEIGHT - PAD - ((v - min) / (max - min)) * (HEIGHT - 2 * PAD),
  };
});

/** One path segment per run of known values; gaps lift the pen. */
const path = computed(() => {
  const { x, y } = scale.value;
  let d = "";
  let pen = false;
  props.values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
});

const thresholdY = computed(() =>
  props.threshold === null || props.threshold === undefined ? null : scale.value.y(props.threshold).toFixed(1),
);
</script>

<template>
  <svg :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" class="spark" role="img" :aria-label="label">
    <title>{{ label }}</title>
    <line v-if="thresholdY !== null" x1="0" :x2="WIDTH" :y1="thresholdY" :y2="thresholdY" class="threshold" />
    <path :d="path" class="line" />
  </svg>
</template>

<style scoped>
.spark { display: block; width: 160px; height: 32px; }
.line { fill: none; stroke: var(--primary); stroke-width: 1.4; }
.threshold { stroke: var(--crit); stroke-width: 1; stroke-dasharray: 3 2; }
</style>
