<script setup lang="ts">
/** Left icon rail: home and projects, plus the report views while a report is open. */
import { computed } from "vue";
import { useRoute } from "vue-router";

const REPORT_VIEWS: Record<string, string> = { evidence: "findings" };

const route = useRoute();

const reportId = computed(() => (route.path.startsWith("/reports/") ? String(route.params.id) : null));
const reportView = computed(() => REPORT_VIEWS[String(route.name)] ?? String(route.name));
const onProjects = computed(() => route.path === "/" || route.path.startsWith("/projects"));
</script>

<template>
  <nav class="rail" aria-label="App">
    <RouterLink to="/" class="logo" aria-label="DevOps AI Assistant home" title="DevOps AI Assistant">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 15l3-4 3 2 4-6" /></svg>
    </RouterLink>
    <RouterLink to="/" :class="{ on: onProjects }" aria-label="Projects" title="Projects">
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 4.5h12M3 9h12M3 13.5h12" /></svg>
    </RouterLink>
    <template v-if="reportId">
      <span class="sep" aria-hidden="true" />
      <RouterLink :to="`/reports/${reportId}`" :class="{ on: reportView === 'overview' }" aria-label="Report overview" title="Overview">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="2" y="2" width="6" height="6" rx="1" /><rect x="10" y="2" width="6" height="6" rx="1" /><rect x="2" y="10" width="6" height="6" rx="1" /><rect x="10" y="10" width="6" height="6" rx="1" /></svg>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/findings`" :class="{ on: reportView === 'findings' }" aria-label="Report findings" title="Findings">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 12.5V8a4.5 4.5 0 0 1 9 0v4.5l1.5 1.5H3z" /><path d="M7.5 16h3" /></svg>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/trends`" :class="{ on: reportView === 'trends' }" aria-label="Report trends" title="Trends">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 15h12" /><path d="M5 12V9M9 12V5M13 12V7" /></svg>
      </RouterLink>
    </template>
  </nav>
</template>

<style scoped>
.rail { flex: 0 0 56px; background: var(--panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 8px 0; }
.rail a { width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; border-radius: var(--radius); color: var(--muted); }
.rail a:hover { background: var(--hover); color: var(--strong); }
.rail .logo { color: var(--primary); margin-bottom: 8px; }
.rail .on { color: var(--strong); background: var(--hover); box-shadow: inset 3px 0 0 var(--primary); }
.sep { width: 24px; height: 1px; background: var(--border); margin: 4px 0; }
@media (max-width: 700px) {
  .rail { flex: 1 1 100%; flex-direction: row; justify-content: center; border-right: none; border-bottom: 1px solid var(--border); padding: 4px 0; }
  .rail .logo { margin: 0 8px 0 0; }
  .rail .on { box-shadow: inset 0 -3px 0 var(--primary); }
  .sep { width: 1px; height: 24px; margin: 0 4px; }
}
</style>
