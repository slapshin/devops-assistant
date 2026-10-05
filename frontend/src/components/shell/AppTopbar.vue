<script setup lang="ts">
/** Breadcrumbs on the left, page badges (slot) and the theme switch on the right. The last crumb is the current page. */
import ThemeSwitch from "../ThemeSwitch.vue";
import type { Crumb } from "./crumbs";

defineProps<{ crumbs: Crumb[] }>();
</script>

<template>
  <header class="topbar">
    <nav class="crumbs" aria-label="Breadcrumb">
      <template v-for="(c, i) in crumbs" :key="i">
        <span v-if="i > 0" aria-hidden="true">›</span>
        <strong v-if="i === crumbs.length - 1" :class="{ mono: c.mono }" aria-current="page">{{ c.label }}</strong>
        <RouterLink v-else-if="c.to" :to="c.to" :class="{ mono: c.mono }">{{ c.label }}</RouterLink>
        <span v-else :class="{ mono: c.mono }">{{ c.label }}</span>
      </template>
    </nav>
    <span class="grow" />
    <slot />
    <ThemeSwitch />
  </header>
</template>

<style scoped>
.topbar { min-height: 48px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; padding: 0 16px; }
.crumbs { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--muted); min-width: 0; flex-wrap: wrap; }
.crumbs a { color: var(--muted); text-decoration: none; }
.crumbs a:hover { color: var(--strong); }
.crumbs strong { color: var(--strong); font-weight: 500; }
@media (max-width: 700px) {
  .topbar { padding: 8px 16px; flex-wrap: wrap; }
}
</style>
