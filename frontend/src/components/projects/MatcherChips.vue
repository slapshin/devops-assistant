<script setup lang="ts">
import type { LabelMatcher } from "../../api/client";

/** Tag filters of other sources are always equalities, so they may omit the operator. */
type Chip = Omit<LabelMatcher, "op"> & { op?: LabelMatcher["op"] };

withDefaults(defineProps<{ matchers: Chip[]; label?: string }>(), { label: "Label matchers" });
</script>

<template>
  <ul class="chips" :aria-label="label">
    <li v-for="(m, i) in matchers" :key="i" class="chip mono">{{ m.name }}{{ m.op ?? "=" }}<span class="value">"{{ m.value }}"</span></li>
  </ul>
</template>

<style scoped>
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; list-style: none; }
.chip { padding: 0 8px; border-radius: 999px; background: var(--grid); color: var(--text); font-size: 0.8rem; line-height: 22px; }
.value { color: var(--text); }
</style>
