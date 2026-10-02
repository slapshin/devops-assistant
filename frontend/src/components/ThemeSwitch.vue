<script setup lang="ts">
import { THEME_CHOICES, theme } from "../lib/theme";
</script>

<template>
  <div class="switch" role="group" aria-label="Theme">
    <button
      v-for="c in THEME_CHOICES"
      :key="c.value"
      type="button"
      :aria-pressed="theme === c.value"
      @click="theme = c.value"
    >
      {{ c.label }}
    </button>
  </div>
</template>

<style scoped>
.switch { display: flex; }
.switch button { border-radius: 0; border-left-width: 0; background: var(--bg); font-size: 0.85rem; min-height: 36px; }
.switch button:first-child { border-left-width: 1px; border-radius: var(--radius) 0 0 var(--radius); }
.switch button:last-child { border-radius: 0 var(--radius) var(--radius) 0; }
.switch button[aria-pressed="true"] { background: var(--inv-bg); color: var(--inv-fg); border-color: var(--inv-bg); }
</style>
