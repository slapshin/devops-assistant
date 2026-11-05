<script setup lang="ts">
import AppRail from "./components/shell/AppRail.vue";
import { railHidden } from "./lib/rail";
</script>

<template>
  <div class="shell">
    <AppRail v-if="!railHidden" />
    <button
      v-else
      type="button"
      class="rail-show"
      aria-label="Show navigation"
      title="Show navigation"
      @click="railHidden = false"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M6 2.5v11M9 6.5l1.5 1.5L9 9.5" /></svg>
    </button>
    <div class="content">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.shell { display: flex; flex-wrap: wrap; min-height: 100vh; }
.content { flex: 999 1 560px; min-width: 0; display: flex; flex-direction: column; }
.rail-show { position: fixed; left: 8px; bottom: 8px; z-index: 20; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; padding: 0; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); color: var(--muted); cursor: pointer; }
.rail-show:hover { background: var(--hover); color: var(--strong); }
</style>
