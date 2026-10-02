<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { Finding } from "../../api/client";
import ConfidenceBadge from "../../components/ConfidenceBadge.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { recurrenceText, usualText } from "../../lib/findings";
import { FAMILY_LABELS, SEVERITIES, formatDuration, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";
import EvidencePanel from "./EvidencePanel.vue";

/** Shortcuts must not fire while the user is typing into a form field. */
const TYPING_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA"]);
const STEP_BY_KEY: Record<string, number> = { j: 1, k: -1 };

const props = defineProps<{ id: string; findingId?: string }>();

const route = useRoute();
const router = useRouter();
const { report, findingById } = useReportContext();
const showHelp = ref(false);
const rowRefs = new Map<string, HTMLElement>();
let lastOpenedId: string | null = null;

const category = computed(() => (route.query.category as string) || "");
const severity = computed(() => (route.query.severity as string) || "");
const entity = computed(() => (route.query.entity as string) || "");
const recurrence = computed(() => (route.query.recurrence as string) || "");

const families = computed(() => [...new Set(report.value.findings.map((f) => f.family))]);
const filtered = computed(() => report.value.findings.filter(matchesFilters));
const selected = computed(() => (props.findingId ? findingById.value.get(props.findingId) ?? null : null));
const listLink = computed(() => ({ path: `/reports/${props.id}/findings`, query: route.query }));
const recurrenceOptions = computed(() => {
  const all = report.value.findings;
  const newCount = all.filter((f) => f.recurrence === "new").length;
  return [
    { value: "", label: `All ${all.length}` },
    { value: "new", label: `New today · ${newCount}` },
    { value: "seen", label: `Happened before · ${all.length - newCount}` },
  ];
});

function matchesFilters(f: Finding): boolean {
  return (
    (!category.value || f.family === category.value) &&
    (!severity.value || f.severity === severity.value) &&
    (!recurrence.value || (recurrence.value === "new") === (f.recurrence === "new")) &&
    (!entity.value || f.entity.display_name.toLowerCase().includes(entity.value.toLowerCase()))
  );
}

function setFilter(key: string, value: string) {
  const query = { ...route.query, [key]: value || undefined };
  void router.replace({ path: `/reports/${props.id}/findings`, query });
}

const detailLink = (targetId: string) => ({ path: `/reports/${props.id}/findings/${targetId}`, query: route.query });

/** j/k move through the filtered list, clamped at both ends. */
function stepSelection(step: number) {
  const list = filtered.value;
  if (!list.length) return;

  const index = list.findIndex((f) => f.finding_id === props.findingId);
  const nextIndex = Math.min(list.length - 1, Math.max(0, index + step));
  const nextFinding = list[nextIndex];
  if (nextFinding) void router.push(detailLink(nextFinding.finding_id));
}

function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target && TYPING_TAGS.has(target.tagName)) return;

  if (event.key === "?") {
    showHelp.value = !showHelp.value;
    return;
  }
  if (event.key === "Escape" && selected.value) {
    event.preventDefault();
    void router.push(listLink.value);
    return;
  }
  const step = STEP_BY_KEY[event.key];
  if (step !== undefined) stepSelection(step);
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));

// Focus returns to the row that opened the detail when it closes.
watch(
  () => props.findingId,
  async (now, before) => {
    if (now) lastOpenedId = now;
    if (!now && before) {
      await nextTick();
      rowRefs.get(lastOpenedId ?? before)?.focus();
    }
  },
);
</script>

<template>
  <div class="findings" :class="{ 'has-detail': !!selected }">
    <section class="list" aria-labelledby="findings-title">
      <h2 id="findings-title">Findings in the latest 24 hours</h2>
      <div class="pills" role="group" aria-label="Recurrence">
        <button
          v-for="o in recurrenceOptions"
          :key="o.value"
          type="button"
          :aria-pressed="recurrence === o.value"
          @click="setFilter('recurrence', o.value)"
        >
          {{ o.label }}
        </button>
      </div>
      <div class="row filters">
        <label>Category
          <select :value="category" @change="setFilter('category', ($event.target as HTMLSelectElement).value)">
            <option value="">All</option>
            <option v-for="fam in families" :key="fam" :value="fam">{{ FAMILY_LABELS[fam] ?? fam }}</option>
          </select>
        </label>
        <label>Severity
          <select :value="severity" @change="setFilter('severity', ($event.target as HTMLSelectElement).value)">
            <option value="">All</option>
            <option v-for="s in SEVERITIES" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label>Entity
          <input type="search" :value="entity" placeholder="filter by name" @input="setFilter('entity', ($event.target as HTMLInputElement).value)">
        </label>
        <button type="button" class="link" :aria-expanded="showHelp" @click="showHelp = !showHelp">Keyboard shortcuts</button>
      </div>
      <p v-if="showHelp" class="card small" role="note">
        <kbd>j</kbd>/<kbd>k</kbd> next/previous finding · <kbd>Esc</kbd> close detail · <kbd>?</kbd> toggle this help. Shortcuts are ignored while typing.
      </p>

      <p v-if="report.findings.length === 0" class="card">No findings in the latest 24 hours. Check the Overview coverage table for signals that could not be evaluated.</p>
      <div v-else-if="filtered.length === 0" class="card">
        No findings match these filters.
        <button type="button" @click="router.replace(`/reports/${id}/findings`)">Clear filters</button>
      </div>
      <ul v-else class="rows">
        <li v-for="f in filtered" :key="f.finding_id">
          <RouterLink
            :ref="(el: unknown) => { if (el) rowRefs.set(f.finding_id, (el as { $el: HTMLElement }).$el) }"
            :to="detailLink(f.finding_id)"
            class="frow"
            :aria-current="f.finding_id === findingId ? 'true' : undefined"
          >
            <SeverityChip :severity="f.severity" class="sev" />
            <span class="main">
              <span class="t">{{ f.title }}</span>
              <span class="e">{{ f.entity.display_name }}</span>
              <span class="w" :title="utcTooltip(f.start)">{{ formatTime(f.start) }} · {{ formatDuration(f.duration_seconds) }} · {{ recurrenceText(f) }}</span>
            </span>
            <span class="v" :class="`sev-${f.severity}`">
              <strong>{{ formatValue(f.observed.value, f.observed.unit) }}</strong>
              <span class="u">{{ usualText(f) || "—" }}</span>
              <ConfidenceBadge :confidence="f.confidence" />
            </span>
          </RouterLink>
        </li>
      </ul>
    </section>

    <section v-if="findingId && !selected" class="detail card" role="alert">
      This finding is not part of the report. <RouterLink :to="listLink">Back to findings</RouterLink>
    </section>
    <EvidencePanel v-if="selected" :key="selected.finding_id" class="detail" :finding="selected" :close-to="listLink" />
  </div>
</template>

<style scoped>
.findings { display: grid; grid-template-columns: minmax(0, 1fr); gap: calc(var(--space) * 2.5); position: relative; align-items: start; }
.list h2 { margin-top: 0; }
.pills { display: flex; gap: var(--space); flex-wrap: wrap; margin-bottom: var(--space); }
.pills button { border-radius: 999px; background: var(--bg); font-size: 0.85rem; min-height: 34px; }
.pills button[aria-pressed="true"] { background: var(--inv-bg); color: var(--inv-fg); border-color: var(--inv-bg); }
.filters label { display: flex; flex-direction: column; font-size: 0.85rem; color: var(--text-muted); }
.filters { align-items: flex-end; margin-bottom: calc(var(--space) * 1.5); }
.rows { list-style: none; padding: 0; margin: 0; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.rows li + li { border-top: 1px solid var(--subtle); }
.frow {
  display: grid; grid-template-columns: 90px minmax(0, 1fr) auto; gap: 14px;
  padding: 14px 16px; color: inherit; text-decoration: none; align-items: center;
}
.frow:hover { background: var(--surface); }
.frow[aria-current="true"] { background: var(--info-bg); box-shadow: inset 3px 0 0 var(--focus); }
.sev { justify-self: start; }
.main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.t { font-weight: 600; }
.e { font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-muted); }
.w { font-size: 0.8rem; color: var(--text-muted); }
.v { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; text-align: right; }
.v strong { font-size: 1.2rem; }
.v.sev-critical strong { color: var(--sev-critical); }
.v.sev-high strong { color: var(--sev-high); }
.v.sev-medium strong { color: var(--sev-medium); }
.u { font-size: 0.8rem; color: var(--text-muted); }
.small { font-size: 0.85rem; }
@media (min-width: 1200px) {
  .findings.has-detail { grid-template-columns: minmax(0, 55fr) minmax(0, 45fr); }
}
@media (min-width: 900px) and (max-width: 1199px) {
  .findings.has-detail .detail { position: absolute; top: 0; right: 0; width: 70%; box-shadow: -8px 0 24px rgba(0, 0, 0, 0.25); z-index: 2; }
}
@media (max-width: 899px) {
  .findings.has-detail .list { display: none; }
  .frow { grid-template-columns: minmax(0, 1fr); }
  .v { align-items: flex-start; text-align: left; }
}
</style>
