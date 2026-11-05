<script setup lang="ts">
/**
 * Left navigation: home, projects, credentials and notification channels, plus the report views while a report is open.
 * Expanded it shows each item's icon and title; collapsed it is an icon-only rail (the choice is kept per browser).
 */
import { computed } from "vue";
import { useRoute } from "vue-router";
import { railCollapsed } from "../../lib/rail";

const REPORT_VIEWS: Record<string, string> = { evidence: "findings" };

const route = useRoute();

const reportId = computed(() => (route.path.startsWith("/reports/") ? String(route.params.id) : null));
const reportView = computed(() => REPORT_VIEWS[String(route.name)] ?? String(route.name));
const onProjects = computed(() => route.path === "/" || route.path.startsWith("/projects"));
const onCredentials = computed(() => route.path.startsWith("/credentials"));
const onChannels = computed(() => route.path.startsWith("/notification-channels"));

/** Tooltips only where the title is not visible. */
const tip = (title: string) => (railCollapsed.value ? title : undefined);
const toggleLabel = computed(() => (railCollapsed.value ? "Expand navigation" : "Collapse navigation"));
</script>

<template>
  <nav class="rail" :class="{ collapsed: railCollapsed }" aria-label="App">
    <RouterLink to="/" class="logo" aria-label="DevOps AI Assistant home" :title="tip('DevOps AI Assistant')">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 15l3-4 3 2 4-6" /></svg>
      <span class="label brand">DevOps AI Assistant</span>
    </RouterLink>
    <RouterLink to="/" :class="{ on: onProjects }" aria-label="Projects" :title="tip('Projects')">
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 4.5h12M3 9h12M3 13.5h12" /></svg>
      <span class="label">Projects</span>
    </RouterLink>
    <RouterLink to="/credentials" :class="{ on: onCredentials }" aria-label="Credentials" :title="tip('Credentials')">
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="11.5" r="3.5" /><path d="M8.5 9l6-6M12.5 5l2 2M10.5 7l1.5 1.5" /></svg>
      <span class="label">Credentials</span>
    </RouterLink>
    <RouterLink to="/notification-channels" :class="{ on: onChannels }" aria-label="Notification channels" :title="tip('Notification channels')">
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8.5l13-5.5-4 12.5-3-4.5z" /><path d="M8.5 11l7-8" /></svg>
      <span class="label">Notification channels</span>
    </RouterLink>
    <template v-if="reportId">
      <span class="sep" aria-hidden="true" />
      <span class="label section" aria-hidden="true">Report</span>
      <RouterLink :to="`/reports/${reportId}`" :class="{ on: reportView === 'overview' }" aria-label="Report overview" :title="tip('Overview')">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="2" y="2" width="6" height="6" rx="1" /><rect x="10" y="2" width="6" height="6" rx="1" /><rect x="2" y="10" width="6" height="6" rx="1" /><rect x="10" y="10" width="6" height="6" rx="1" /></svg>
        <span class="label">Overview</span>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/findings`" :class="{ on: reportView === 'findings' }" aria-label="Report findings" :title="tip('Findings')">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 12.5V8a4.5 4.5 0 0 1 9 0v4.5l1.5 1.5H3z" /><path d="M7.5 16h3" /></svg>
        <span class="label">Findings</span>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/timeline`" :class="{ on: reportView === 'timeline' }" aria-label="Report timeline" :title="tip('Timeline')">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M2 4h6M6 9h8M4 14h5" /><path d="M2 16V2" /></svg>
        <span class="label">Timeline</span>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/trends`" :class="{ on: reportView === 'trends' }" aria-label="Report trends" :title="tip('Trends')">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 15h12" /><path d="M5 12V9M9 12V5M13 12V7" /></svg>
        <span class="label">Trends</span>
      </RouterLink>
      <RouterLink :to="`/reports/${reportId}/scope`" :class="{ on: reportView === 'scope' }" aria-label="Report scope" :title="tip('Scope')">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 3.5h13l-5 6v5l-3 1.5V9.5z" /></svg>
        <span class="label">Scope</span>
      </RouterLink>
    </template>
    <button
      type="button"
      class="toggle"
      :aria-label="toggleLabel"
      :aria-expanded="!railCollapsed"
      :title="tip(toggleLabel)"
      @click="railCollapsed = !railCollapsed"
    >
      <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M6 2.5v11" />
        <path v-if="railCollapsed" d="M9 6.5l1.5 1.5L9 9.5" /><path v-else d="M10.5 6.5L9 8l1.5 1.5" />
      </svg>
      <span class="label">Collapse</span>
    </button>
  </nav>
</template>

<style scoped>
.rail { flex: 0 0 220px; position: sticky; top: 0; height: 100vh; box-sizing: border-box; overflow-y: auto; background: var(--panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 2px; padding: 8px; }
.rail a, .rail .toggle { height: 40px; flex: 0 0 auto; display: flex; align-items: center; gap: 12px; padding: 0 11px; border-radius: var(--radius); color: var(--muted); text-decoration: none; font-size: 14px; white-space: nowrap; }
.rail svg { flex: 0 0 auto; }
.rail a:hover, .rail .toggle:hover { background: var(--hover); color: var(--strong); }
.rail .logo { color: var(--primary); margin-bottom: 8px; padding: 0 8px; }
.rail .brand { color: var(--strong); font-weight: 600; }
.rail .on { color: var(--strong); background: var(--hover); box-shadow: inset 3px 0 0 var(--primary); }
.rail .toggle { margin-top: auto; width: 100%; border: none; background: none; font: inherit; font-size: 14px; cursor: pointer; justify-content: flex-start; }
.label { overflow: hidden; text-overflow: ellipsis; }
.section { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); padding: 8px 11px 4px; }
.sep { display: none; }

.rail.collapsed { flex-basis: 56px; align-items: center; padding: 8px 0; gap: 4px; }
.rail.collapsed a, .rail.collapsed .toggle { width: 40px; padding: 0; justify-content: center; }
.rail.collapsed .label { display: none; }
.rail.collapsed .sep { display: block; width: 24px; height: 1px; background: var(--border); margin: 4px 0; }

@media (max-width: 700px) {
  .rail, .rail.collapsed { flex: 1 1 100%; position: static; height: auto; overflow: visible; flex-direction: row; align-items: center; justify-content: center; gap: 4px; border-right: none; border-bottom: 1px solid var(--border); padding: 4px 0; }
  .rail a { width: 40px; padding: 0; justify-content: center; }
  .rail .logo { margin: 0 8px 0 0; }
  .rail .label, .rail .toggle { display: none; }
  .rail .on { box-shadow: inset 0 -3px 0 var(--primary); }
  .rail .sep, .rail.collapsed .sep { display: block; width: 1px; height: 24px; background: var(--border); margin: 0 4px; }
}
</style>
