<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError, type AnalysisReport } from "../../api/client";
import { useReport, useSubmit } from "../../api/queries";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import type { Crumb } from "../../components/shell/crumbs";
import { longestBaselineDays } from "../../lib/findings";
import { formatTime, timezone, utcTooltip } from "../../lib/format";
import { reportSources } from "../../lib/projects";
import { provideReport } from "./context";

const SECONDS_PER_MINUTE = 60;
/** WAI-ARIA tabs pattern: arrow keys wrap, Home/End jump to the ends. */
const TAB_KEY_TARGETS: Record<string, (index: number, count: number) => number> = {
  ArrowRight: (index, count) => (index + 1) % count,
  ArrowLeft: (index, count) => (index - 1 + count) % count,
  Home: () => 0,
  End: (_, count) => count - 1,
};

const props = defineProps<{ id: string }>();

const route = useRoute();
const router = useRouter();
const query = useReport(() => props.id);
const submit = useSubmit();
const tabRefs = ref<HTMLElement[]>([]);

const report = computed(() => query.data.value as AnalysisReport);
provideReport({
  report,
  findingById: computed(() => new Map((query.data.value?.findings ?? []).map((f) => [f.finding_id, f]))),
  evidenceById: computed(() => new Map((query.data.value?.evidence ?? []).map((e) => [e.evidence_id, e]))),
});

const tabs = computed(() => [
  { name: "overview", label: "Overview", to: `/reports/${props.id}`, count: null },
  { name: "findings", label: "Findings", to: `/reports/${props.id}/findings`, count: query.data.value?.findings.length ?? 0 },
  { name: "timeline", label: "Timeline", to: `/reports/${props.id}/timeline`, count: null },
  { name: "trends", label: "Trends", to: `/reports/${props.id}/trends`, count: null },
]);
const activeTab = computed(() => (route.name === "evidence" ? "findings" : String(route.name)));
const error = computed(() => (query.error.value instanceof ApiError ? query.error.value.problem : null));
const stepMinutes = computed(() => report.value.windows.step_seconds / SECONDS_PER_MINUTE);
const baselineDays = computed(() => longestBaselineDays(report.value.coverage));
const crumbs = computed<Crumb[]>(() => {
  const scope = query.data.value?.scope;
  return [
    { label: "Projects", to: "/" },
    ...(scope ? [{ label: scope.project_name, to: `/projects/${scope.project_id}`, mono: true }] : []),
    { label: tabs.value.find((t) => t.name === activeTab.value)?.label ?? "Report" },
  ];
});

async function onTabKey(event: KeyboardEvent, index: number) {
  const resolveTarget = TAB_KEY_TARGETS[event.key];
  if (!resolveTarget) return;
  event.preventDefault();

  const next = resolveTarget(index, tabs.value.length);
  const tab = tabs.value[next];
  if (!tab) return;

  await router.push(tab.to);
  await nextTick();
  tabRefs.value[next]?.focus();
}

async function runAgain() {
  const scope = query.data.value?.scope;
  if (!scope) return;

  try {
    const submitted = await submit.mutateAsync({ project_id: scope.project_id });
    await router.push(`/analyses/${submitted.analysis.analysis_id}`);
  } catch {
    /* shown from submit.error */
  }
}
</script>

<template>
  <AppTopbar :crumbs="crumbs">
    <span v-if="report && reportSources(report).some((s) => s.backend === 'synthetic')" class="badge">Synthetic data</span>
  </AppTopbar>

  <main v-if="query.isPending.value" aria-busy="true" class="page">
    <div class="skeleton" style="width: 60%" /><div class="skeleton" style="width: 40%" /><div class="skeleton" />
  </main>

  <main v-else-if="error" class="page">
    <section role="alert" class="banner error">
      <template v-if="error.code === 'schema_unsupported'">This report was saved by an incompatible version ({{ error.detail }}).</template>
      <template v-else-if="error.code === 'report_not_ready'">
        This analysis is still running. <RouterLink :to="`/analyses/${id}`">View progress</RouterLink>.
      </template>
      <template v-else-if="error.code === 'report_unavailable'">No report was saved for this analysis: {{ error.detail }}</template>
      <template v-else-if="error.code === 'analysis_not_found'">This report does not exist.</template>
      <template v-else>{{ error.title }}. <button type="button" @click="query.refetch()">Retry</button></template>
      <RouterLink to="/">Projects</RouterLink>
    </section>
  </main>

  <template v-else-if="report">
    <div class="ctl">
      <span v-if="baselineDays" class="var"><span>baseline</span><span>{{ baselineDays }} days</span></span>
      <span class="grow" />
      <span class="lbl meta">
        {{ report.state === "partial" ? "Partial" : "Completed" }} ·
        <span :title="`config ${report.config_hash}`">{{ report.detector_version }}</span> ·
        saved <time :datetime="report.generated_at" :title="utcTooltip(report.generated_at)">{{ formatTime(report.generated_at) }}</time>
      </span>
      <div class="var" :title="`Latest 24 h, ${stepMinutes}-min steps`">
        <span>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="5.5" /><path d="M7 4v3l2 1.5" /></svg>
          <span class="sr-only">Window:</span>
          <time :datetime="report.windows.latest_day.start" :title="utcTooltip(report.windows.latest_day.start)">{{ formatTime(report.windows.latest_day.start, true, false) }}</time>
          –
          <time :datetime="report.windows.latest_day.end" :title="utcTooltip(report.windows.latest_day.end)">{{ formatTime(report.windows.latest_day.end, true, false) }}</time>
        </span>
        <select v-model="timezone" aria-label="Time zone" class="tz">
          <option value="local">Local</option>
          <option value="utc">UTC</option>
        </select>
      </div>
      <button type="button" class="primary" :disabled="submit.isPending.value" @click="runAgain">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M12 7a5 5 0 1 1-1.5-3.6" /><path d="M12 2v3H9" /></svg>
        Run again
      </button>
    </div>
    <div v-if="submit.error.value || report.state === 'partial'" class="notes">
      <p v-if="submit.error.value" role="alert" class="banner error">Could not start a new analysis: {{ submit.error.value.message }}</p>
      <p v-if="report.state === 'partial'" class="banner" role="status">
        Some signals could not be collected — see <RouterLink :to="{ path: `/reports/${id}`, hash: '#coverage' }">Coverage</RouterLink>.
      </p>
    </div>
    <nav class="tabs-bar" aria-label="Report">
      <div role="tablist" aria-label="Report views" class="tabs">
        <RouterLink
          v-for="(tab, i) in tabs"
          :key="tab.name"
          :ref="(el: unknown) => { if (el) tabRefs[i] = (el as { $el: HTMLElement }).$el }"
          :to="tab.to"
          role="tab"
          :aria-selected="activeTab === tab.name"
          :tabindex="activeTab === tab.name ? 0 : -1"
          class="tab"
          @keydown="onTabKey($event, i)"
        >
          {{ tab.label }} <span v-if="tab.count !== null" class="count">{{ tab.count }}</span>
        </RouterLink>
      </div>
    </nav>
    <main role="tabpanel" :aria-label="tabs.find((t) => t.name === activeTab)?.label">
      <RouterView />
    </main>
  </template>
</template>

<style scoped>
.ctl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 16px; background: var(--canvas); }
.meta { white-space: nowrap; }
.var .tz { min-width: 72px; }
.notes { display: flex; flex-direction: column; gap: 8px; padding: 0 16px 10px; background: var(--canvas); }
.tabs-bar { padding: 0 16px; border-bottom: 1px solid var(--border); background: var(--canvas); overflow-x: auto; }
.tabs { display: flex; gap: 4px; }
.tab { display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px; font-size: 14px; text-decoration: none; color: var(--muted); border-bottom: 2px solid transparent; white-space: nowrap; }
.tab:hover { color: var(--strong); }
.tab[aria-selected="true"] { color: var(--strong); font-weight: 500; border-bottom-color: var(--primary); }
</style>
