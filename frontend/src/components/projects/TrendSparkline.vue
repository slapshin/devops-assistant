<script setup lang="ts">
/** 14 daily bars of anomaly episodes (oldest first); days without enough data show as a dashed stub. */
import { computed } from "vue";

const WIDTH = 140;
const HEIGHT = 32;
const GAP = 2;
const MIN_BAR = 1.5;

const props = defineProps<{ days: (number | null)[] }>();

const bars = computed(() => {
  const max = Math.max(1, ...props.days.map((d) => d ?? 0));
  const width = (WIDTH - GAP * (props.days.length - 1)) / props.days.length;
  return props.days.map((count, i) => {
    const x = i * (width + GAP);
    if (count === null) return { x, width, y: HEIGHT - MIN_BAR, height: MIN_BAR, missing: true };
    const height = count ? Math.max(MIN_BAR * 2, (count / max) * HEIGHT) : MIN_BAR;
    return { x, width, y: HEIGHT - height, height, missing: false, empty: count === 0 };
  });
});

const summary = computed(() => {
  const known = props.days.filter((d): d is number => d !== null);
  const missing = props.days.length - known.length;
  const total = known.reduce((a, b) => a + b, 0);
  const latest = props.days.at(-1);
  return (
    `Anomaly episodes over ${props.days.length} days: ${total} in total, ` +
    `${latest === null || latest === undefined ? "not evaluated" : latest} on the latest day` +
    (missing ? `, ${missing} days without enough data` : "")
  );
});
</script>

<template>
  <svg :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" class="trend" role="img" :aria-label="summary">
    <title>{{ summary }}</title>
    <rect
      v-for="(b, i) in bars"
      :key="i"
      :x="b.x"
      :y="b.y"
      :width="b.width"
      :height="b.height"
      :class="{ missing: b.missing, empty: b.empty, latest: i === bars.length - 1 }"
    />
  </svg>
</template>

<style scoped>
.trend { display: block; width: 140px; height: 32px; }
rect { fill: var(--bar-high); }
rect.empty { fill: var(--border); }
rect.missing { fill: none; stroke: var(--text-muted); stroke-dasharray: 2 2; stroke-width: 1; }
rect.latest:not(.missing, .empty) { fill: var(--bar-critical); }
</style>
