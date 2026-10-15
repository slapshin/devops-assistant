<script setup lang="ts">
import { computed } from "vue";
import type { EventTotal } from "../api/client";
import { formatTime } from "../lib/format";

const MS_PER_DAY = 86_400_000;
const PERCENT = 100;
/** What one counted entity is, per source. */
const ENTITY_NAMES: Record<string, [string, string]> = {
  sentry: ["project", "projects"],
  wazuh: ["agent", "agents"],
  grafana: ["alert rule", "alert rules"],
};

const props = defineProps<{ total: EventTotal; end: string }>();

const latest = computed(() => props.total.days[0]);
const partial = computed(() => latest.value?.count !== null && (latest.value?.coverage ?? 0) < 1);
const value = computed(() => {
  const count = latest.value?.count;
  if (count === null || count === undefined) return "—";

  return `${partial.value ? "≥ " : ""}${count.toLocaleString()}`;
});
const entities = computed(() => {
  const [one, many] = ENTITY_NAMES[props.total.source] ?? ["source", "sources"];
  return `${props.total.entities} ${props.total.entities === 1 ? one : many}`;
});
const earlier = computed(() => props.total.days.slice(1).flatMap((d) => (d.count === null ? [] : [d.count])));
const usual = computed(() => median(earlier.value));
const detail = computed(() => {
  const count = latest.value?.count;
  if (count === null || count === undefined) return "no data in the latest 24 h";

  const parts = [comparison(count)];
  if (partial.value) parts.push("partial data");
  return parts.join(" · ");
});
/** Oldest day first, so the latest bar is on the right. */
const bars = computed(() => {
  const peak = Math.max(1, ...props.total.days.map((d) => d.count ?? 0));
  const end = new Date(props.end).getTime();
  return [...props.total.days].reverse().map((d) => {
    const start = new Date(end - (d.bucket_index + 1) * MS_PER_DAY).toISOString();
    const stop = new Date(end - d.bucket_index * MS_PER_DAY).toISOString();
    const label = d.count === null ? "unknown" : d.count.toLocaleString();
    return {
      key: d.bucket_index,
      height: d.count === null ? PERCENT : (d.count / peak) * PERCENT,
      unknown: d.count === null,
      latest: d.bucket_index === 0,
      title: `${formatTime(start)} – ${formatTime(stop)}: ${label}`,
    };
  });
});

function comparison(count: number): string {
  const base = usual.value;
  if (base === null) return "no earlier days to compare";

  const days = `median ${base.toLocaleString()}/day over ${earlier.value.length} ${earlier.value.length === 1 ? "day" : "days"} before`;
  if (base === 0) return count === 0 ? `as usual (${days})` : days;

  const change = Math.round(((count - base) / base) * PERCENT);
  return `${change > 0 ? "+" : ""}${change} % vs. ${days}`;
}

function median(values: number[]): number | null {
  if (!values.length) return null;

  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const upper = sorted[mid] ?? 0;
  return sorted.length % 2 ? upper : Math.round(((sorted[mid - 1] ?? 0) + upper) / 2);
}
</script>

<template>
  <section class="panel" :aria-label="total.title">
    <div class="ph"><h2>{{ total.title }}</h2><span class="sub">· latest 24 h · {{ entities }}</span></div>
    <div class="stat">
      <div class="v">{{ value }}</div>
      <div class="d">{{ detail }}</div>
      <div class="bars" role="img" :aria-label="`${total.title} per day, last ${total.days.length} days`">
        <span v-for="b in bars" :key="b.key" class="bar" :class="{ latest: b.latest, unknown: b.unknown }" :style="{ height: `${b.height}%` }" :title="b.title" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.bars { display: flex; align-items: flex-end; gap: 2px; height: 28px; margin-top: 8px; }
.bar { flex: 1 1 0; min-height: 1px; border-radius: 1px; background: var(--f-low); opacity: 0.55; }
.bar.latest { background: var(--s1); opacity: 1; }
.bar.unknown { background: repeating-linear-gradient(135deg, var(--f-none) 0 3px, transparent 3px 6px); opacity: 1; }
</style>
