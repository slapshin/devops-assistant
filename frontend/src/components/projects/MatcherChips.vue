<script setup lang="ts">
import type { LabelMatcher } from "../../api/client";

defineProps<{ matchers: LabelMatcher[] }>();
</script>

<template>
  <ul class="chips" aria-label="Label matchers">
    <li v-for="m in matchers" :key="m.name" class="chip mono">{{ m.name }}=<span class="value">"{{ m.value }}"</span></li>
  </ul>
</template>

<style scoped>
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; list-style: none; }
.chip { padding: 0 8px; border-radius: 999px; background: var(--subtle); color: var(--text-2); font-size: 0.8rem; line-height: 22px; }
.value { color: var(--text); }
</style>
