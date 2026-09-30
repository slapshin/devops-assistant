<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError, type AnalysisReport } from "../../api/client";
import { useReport, useSubmit } from "../../api/queries";
import { formatTime, timezone, utcTooltip } from "../../lib/format";
import { provideReport } from "./context";

const props = defineProps<{ id: string }>();
const route = useRoute();
const router = useRouter();
const query = useReport(() => props.id);
const submit = useSubmit();

const report = computed(() => query.data.value as AnalysisReport);
provideReport({
  report,
  findingById: computed(() => new Map((query.data.value?.findings ?? []).map((f) => [f.finding_id, f]))),
  evidenceById: computed(() => new Map((query.data.value?.evidence ?? []).map((e) => [e.evidence_id, e]))),
});

const tabs = computed(() => [
  { name: "overview", label: "Overview", to: `/reports/${props.id}` },
  { name: "findings", label: `Findings (${query.data.value?.findings.length ?? 0})`, to: `/reports/${props.id}/findings` },
  { name: "trends", label: "Trends", to: `/reports/${props.id}/trends` },
]);
const activeTab = computed(() => (route.name === "evidence" ? "findings" : String(route.name)));
const tabRefs = ref<HTMLElement[]>([]);

async function onTabKey(event: KeyboardEvent, index: number) {
  const n = tabs.value.length;
  const next =
    event.key === "ArrowRight" ? (index + 1) % n
    : event.key === "ArrowLeft" ? (index - 1 + n) % n
    : event.key === "Home" ? 0
    : event.key === "End" ? n - 1
    : null;
  if (next === null) return;
  event.preventDefault();
  const tab = tabs.value[next];
  if (!tab) return;
  await router.push(tab.to);
  await nextTick();
  tabRefs.value[next]?.focus();
}

const error = computed(() => (query.error.value instanceof ApiError ? query.error.value.problem : null));

async function runAgain() {
  const scope = query.data.value?.scope;
  if (!scope) return;
  try {
    const res = await submit.mutateAsync({ project: scope.project, env: scope.env });
    await router.push(`/analyses/${res.analysis.analysis_id}`);
  } catch {
    /* shown from submit.error */
  }
}
</script>

<template>
  <div v-if="query.isPending.value" aria-busy="true" class="stack">
    <div class="skeleton" style="width: 60%" /><div class="skeleton" style="width: 40%" /><div class="skeleton" />
  </div>

  <section v-else-if="error" role="alert" class="banner error">
    <template v-if="error.code === 'schema_unsupported'">This report was saved by an incompatible version ({{ error.detail }}).</template>
    <template v-else-if="error.code === 'report_not_ready'">
      This analysis is still running. <RouterLink :to="`/analyses/${id}`">View progress</RouterLink>.
    </template>
    <template v-else-if="error.code === 'report_unavailable'">No report was saved for this analysis: {{ error.detail }}</template>
    <template v-else-if="error.code === 'analysis_not_found'">This report does not exist.</template>
    <template v-else>{{ error.title }}. <button type="button" @click="query.refetch()">Retry</button></template>
    <p><RouterLink to="/">New analysis</RouterLink></p>
  </section>

  <template v-else-if="report">
    <header class="frame">
      <div class="scope">
        <strong>{{ report.scope.project }} / {{ report.scope.env }}</strong>
        <span>
          Window:
          <time :datetime="report.windows.latest_day.start" :title="utcTooltip(report.windows.latest_day.start)">{{ formatTime(report.windows.latest_day.start) }}</time>
          –
          <time :datetime="report.windows.latest_day.end" :title="utcTooltip(report.windows.latest_day.end)">{{ formatTime(report.windows.latest_day.end) }}</time>
          (latest 24 h, {{ report.windows.step_seconds / 60 }}-min steps)
        </span>
      </div>
      <div class="meta row">
        <span>Status: {{ report.state === "partial" ? "Partial" : "Completed" }}</span>
        <span class="muted">Detector {{ report.detector_version }} ({{ report.config_hash }})</span>
        <span class="muted">Saved {{ formatTime(report.generated_at) }}</span>
        <span v-if="report.source.backend === 'synthetic'" class="synthetic">Synthetic data</span>
        <label class="tz">
          Times
          <select v-model="timezone" aria-label="Time zone">
            <option value="utc">UTC</option>
            <option value="local">Local</option>
          </select>
        </label>
        <button type="button" :disabled="submit.isPending.value" @click="runAgain">Run again</button>
        <RouterLink :to="{ path: '/', query: { project: report.scope.project, env: report.scope.env } }">New analysis</RouterLink>
      </div>
      <p v-if="submit.error.value" role="alert" class="banner error">Could not start a new analysis: {{ submit.error.value.message }}</p>
      <p v-if="report.state === 'partial'" class="banner" role="status">
        Some signals could not be collected — see <RouterLink :to="{ path: `/reports/${id}`, hash: '#coverage' }">Coverage</RouterLink>.
      </p>
      <nav>
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
            {{ tab.label }}
          </RouterLink>
        </div>
      </nav>
    </header>
    <div role="tabpanel" :aria-label="tabs.find((t) => t.name === activeTab)?.label">
      <RouterView />
    </div>
  </template>
</template>

<style scoped>
.frame { border-bottom: 1px solid var(--border); margin-bottom: calc(var(--space) * 2); }
.scope { display: flex; gap: calc(var(--space) * 2); flex-wrap: wrap; align-items: baseline; }
.meta { margin: var(--space) 0; font-size: 0.9rem; }
.tabs { display: flex; gap: var(--space); }
.tab { padding: var(--space) calc(var(--space) * 1.5); text-decoration: none; color: var(--text); border-bottom: 3px solid transparent; }
.tab[aria-selected="true"] { border-bottom-color: var(--focus); font-weight: 600; }
.synthetic { border: 1px solid var(--status-warn); color: var(--status-warn); border-radius: var(--radius); padding: 0 6px; }
.tz { display: inline-flex; gap: 4px; align-items: center; }
</style>
