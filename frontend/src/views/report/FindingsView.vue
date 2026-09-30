<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import ConfidenceBadge from "../../components/ConfidenceBadge.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { FAMILY_LABELS, SEVERITIES, formatDuration, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";
import EvidencePanel from "./EvidencePanel.vue";

const props = defineProps<{ id: string; findingId?: string }>();
const route = useRoute();
const router = useRouter();
const { report, findingById } = useReportContext();
const showHelp = ref(false);

const category = computed(() => (route.query.category as string) || "");
const severity = computed(() => (route.query.severity as string) || "");
const entity = computed(() => (route.query.entity as string) || "");

function setFilter(key: string, value: string) {
  const query = { ...route.query, [key]: value || undefined };
  void router.replace({ path: `/reports/${props.id}/findings`, query });
}

const families = computed(() => [...new Set(report.value.findings.map((f) => f.family))]);
const filtered = computed(() =>
  report.value.findings.filter(
    (f) =>
      (!category.value || f.family === category.value) &&
      (!severity.value || f.severity === severity.value) &&
      (!entity.value || f.entity.display_name.toLowerCase().includes(entity.value.toLowerCase())),
  ),
);
const selected = computed(() => (props.findingId ? findingById.value.get(props.findingId) ?? null : null));
const detailLink = (fid: string) => ({ path: `/reports/${props.id}/findings/${fid}`, query: route.query });
const listLink = computed(() => ({ path: `/reports/${props.id}/findings`, query: route.query }));
const rowRefs = new Map<string, HTMLElement>();
let lastOpened: string | null = null;

function expectedText(f: (typeof filtered.value)[number]) {
  if (f.expected) return `${formatValue(f.expected.median, f.expected.unit)} (±${formatValue((f.expected.upper - f.expected.lower) / 2, f.expected.unit)})`;
  if (f.threshold !== null) return `heuristic ${formatValue(f.threshold, f.observed.unit)}`;
  return "—";
}

function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
  if (event.key === "?") {
    showHelp.value = !showHelp.value;
    return;
  }
  if (event.key === "Escape" && selected.value) {
    event.preventDefault();
    void router.push(listLink.value);
    return;
  }
  if (event.key !== "j" && event.key !== "k") return;
  const list = filtered.value;
  if (!list.length) return;
  const index = list.findIndex((f) => f.finding_id === props.findingId);
  const next = event.key === "j" ? Math.min(list.length - 1, index + 1) : Math.max(0, index - 1);
  const target2 = list[next];
  if (target2) void router.push(detailLink(target2.finding_id));
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));

// Focus returns to the row that opened the detail when it closes.
watch(
  () => props.findingId,
  async (now, before) => {
    if (now) lastOpened = now;
    if (!now && before) {
      await nextTick();
      rowRefs.get(lastOpened ?? before)?.focus();
    }
  },
);
</script>

<template>
  <div class="findings" :class="{ 'has-detail': !!selected }">
    <section class="list" aria-labelledby="findings-title">
      <h2 id="findings-title">Findings in the latest 24 hours</h2>
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
            <SeverityChip :severity="f.severity" />
            <span class="t">{{ f.title }}</span>
            <span class="e">{{ f.entity.display_name }}</span>
            <span class="w" :title="utcTooltip(f.start)">{{ formatTime(f.start) }} · {{ formatDuration(f.duration_seconds) }}</span>
            <span class="v">{{ formatValue(f.observed.value, f.observed.unit) }} vs {{ expectedText(f) }}</span>
            <ConfidenceBadge :confidence="f.confidence" />
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
.findings { display: grid; grid-template-columns: minmax(0, 1fr); gap: calc(var(--space) * 2); position: relative; }
.filters label { display: flex; flex-direction: column; font-size: 0.85rem; }
.filters { align-items: flex-end; margin-bottom: var(--space); }
.rows { list-style: none; padding: 0; margin: 0; }
.frow {
  display: grid; grid-template-columns: auto minmax(0, 2fr) minmax(0, 2fr) auto; gap: 4px var(--space);
  padding: var(--space); border-bottom: 1px solid var(--border); color: inherit; text-decoration: none; align-items: center;
}
.frow[aria-current="true"] { background: var(--surface); outline: 2px solid var(--focus); }
.frow .e { font-family: var(--font-mono); font-size: 0.85rem; }
.frow .w, .frow .v { font-size: 0.85rem; color: var(--text-muted); }
.frow .v { grid-column: 2 / 4; }
.small { font-size: 0.85rem; }
@media (min-width: 1200px) {
  .findings.has-detail { grid-template-columns: minmax(0, 55fr) minmax(0, 45fr); }
}
@media (min-width: 900px) and (max-width: 1199px) {
  .findings.has-detail .detail { position: absolute; top: 0; right: 0; width: 70%; background: var(--bg); box-shadow: -8px 0 24px rgba(0, 0, 0, 0.25); z-index: 2; }
}
@media (max-width: 899px) {
  .findings.has-detail .list { display: none; }
  .frow { grid-template-columns: auto minmax(0, 1fr); }
  .frow .e, .frow .w, .frow .v { grid-column: 1 / -1; }
}
</style>
