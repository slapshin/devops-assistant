<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { Finding } from "../../api/client";
import SourceIcon from "../../components/projects/SourceIcon.vue";
import SeverityChip from "../../components/SeverityChip.vue";
import { recurrenceText, sourceByFamily } from "../../lib/findings";
import { sourceKindLabel } from "../../lib/projects";
import { FAMILY_LABELS, SEVERITIES, capitalize, formatDuration, formatTime, formatValue, utcTooltip } from "../../lib/format";
import { useReportContext } from "./context";
import EvidencePanel from "./EvidencePanel.vue";

/** Shortcuts must not fire while the user is typing into a form field. */
const TYPING_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA"]);
const STEP_BY_KEY: Record<string, number> = { j: 1, k: -1 };

const props = defineProps<{ id: string; findingId?: string }>();

const route = useRoute();
const router = useRouter();
const { report, findingById } = useReportContext();
const rowRefs = new Map<string, HTMLElement>();
let lastOpenedId: string | null = null;

const category = computed(() => (route.query.category as string) || "");
const severity = computed(() => (route.query.severity as string) || "");
const entity = computed(() => (route.query.entity as string) || "");
const recurrence = computed(() => (route.query.recurrence as string) || "");

const families = computed(() => [...new Set(report.value.findings.map((f) => f.family))]);
const filtered = computed(() => report.value.findings.filter(matchesFilters));
const sourceOf = computed(() => sourceByFamily(report.value.capabilities));
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

/** The usual range, or the heuristic for absolute-only findings. */
function usualCell(f: Finding): string {
  if (f.expected) return `${formatValue(f.expected.lower, f.expected.unit)}–${formatValue(f.expected.upper, f.expected.unit)}`;
  if (f.threshold !== null) return `heuristic ${formatValue(f.threshold, f.observed.unit)}`;
  return "—";
}

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
/** Rows work as an accordion: the open finding's link collapses it again. */
const toggleLink = (targetId: string) => (targetId === props.findingId ? listLink.value : detailLink(targetId));
const toggle = (targetId: string) => void router.push(toggleLink(targetId));

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
  <div class="dash">
    <section class="panel s24" aria-labelledby="findings-title">
      <div class="ph">
        <h2 id="findings-title">Findings</h2>
        <span class="sub">· latest 24 hours · sorted by severity, then peak time</span>
      </div>
      <div class="controls">
        <div class="tg" role="group" aria-label="Recurrence">
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
        <span class="grow" />
        <div class="var">
          <label for="f-category">Category</label>
          <select id="f-category" :value="category" @change="setFilter('category', ($event.target as HTMLSelectElement).value)">
            <option value="">All</option>
            <option v-for="fam in families" :key="fam" :value="fam">{{ FAMILY_LABELS[fam] ?? fam }}</option>
          </select>
        </div>
        <div class="var">
          <label for="f-severity">Severity</label>
          <select id="f-severity" :value="severity" @change="setFilter('severity', ($event.target as HTMLSelectElement).value)">
            <option value="">All</option>
            <option v-for="s in SEVERITIES" :key="s" :value="s">{{ capitalize(s) }}</option>
          </select>
        </div>
        <div class="var">
          <label for="f-entity">Entity</label>
          <input id="f-entity" type="search" :value="entity" placeholder="filter by name" @input="setFilter('entity', ($event.target as HTMLInputElement).value)">
        </div>
      </div>

      <p v-if="report.findings.length === 0" class="empty">No findings in the latest 24 hours. Check the Overview coverage table for signals that could not be evaluated.</p>
      <div v-else-if="filtered.length === 0" class="empty row">
        No findings match these filters.
        <button type="button" @click="router.replace(`/reports/${id}/findings`)">Clear filters</button>
      </div>
      <div v-else class="table-wrap">
        <table class="gt">
          <thead>
            <tr>
              <th scope="col">Severity</th><th scope="col">Finding</th><th scope="col">Source</th><th scope="col">Entity</th><th scope="col">Started</th>
              <th scope="col" class="num">Duration</th><th scope="col" class="num">Peak</th><th scope="col" class="num">Usual</th>
              <th scope="col">Confidence</th><th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="f in filtered" :key="f.finding_id">
              <tr :class="{ sel: f.finding_id === findingId }" @click="toggle(f.finding_id)">
                <td class="first"><SeverityChip :severity="f.severity" /></td>
                <td>
                  <RouterLink
                    :ref="(el: unknown) => { if (el) rowRefs.set(f.finding_id, (el as { $el: HTMLElement }).$el) }"
                    :to="toggleLink(f.finding_id)"
                    :aria-expanded="f.finding_id === findingId"
                    :aria-current="f.finding_id === findingId ? 'true' : undefined"
                    class="toggle"
                    @click.stop
                  >
                    <svg class="chevron" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 2l3 3-3 3" /></svg>
                    {{ f.title }}
                  </RouterLink>
                </td>
                <td>
                  <span v-if="sourceOf.get(f.family)" class="source"><SourceIcon :kind="sourceOf.get(f.family)!" />{{ sourceKindLabel(sourceOf.get(f.family)!) }}</span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="mono muted">{{ f.entity.display_name }}</td>
                <td :title="utcTooltip(f.start)">{{ formatTime(f.start) }}</td>
                <td class="num">{{ formatDuration(f.duration_seconds) }}</td>
                <td class="num peak" :class="`v-${f.severity}`">{{ formatValue(f.observed.value, f.observed.unit) }}</td>
                <td class="num muted">{{ usualCell(f) }}</td>
                <td>{{ capitalize(f.confidence) }}</td>
                <td><span class="tag" :class="{ new: f.recurrence === 'new' }">{{ recurrenceText(f) }}</span></td>
              </tr>
              <tr v-if="selected && f.finding_id === selected.finding_id" class="detail">
                <td colspan="10">
                  <div class="detail-body">
                    <EvidencePanel :key="selected.finding_id" :finding="selected" :close-to="listLink" />
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p class="lbl keys">
        Keyboard: <kbd>j</kbd> / <kbd>k</kbd> next / previous finding · <kbd>Esc</kbd> close the evidence. Shortcuts are ignored while typing.
      </p>
    </section>

    <section v-if="findingId && !selected" class="card s24" role="alert">
      This finding is not part of the report. <RouterLink :to="listLink">Back to findings</RouterLink>
    </section>
    <section v-else-if="selected && !filtered.some((f) => f.finding_id === selected?.finding_id)" class="card s24" role="status">
      This finding is hidden by the current filters. <RouterLink :to="listLink">Close it</RouterLink>
    </section>
  </div>
</template>

<style scoped>
.controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 0 12px 8px; }
.var input { min-width: 160px; }
.empty { margin: 0; padding: 8px 12px 12px; }
.gt > tbody > tr { cursor: pointer; }
.gt > tbody > tr.sel > .first { box-shadow: inset 3px 0 0 var(--primary); }
.toggle { display: inline-flex; align-items: center; gap: 6px; }
.chevron { flex: none; color: var(--muted); transition: transform 0.15s ease; }
.toggle[aria-expanded="true"] .chevron { transform: rotate(90deg); color: var(--primary); }
.gt > tbody > tr.detail { cursor: default; }
.gt > tbody > tr.detail > td { height: auto; padding: 8px; white-space: normal; background: var(--canvas); box-shadow: inset 3px 0 0 var(--primary); }
/* The detail matches the visible scroll area, not the (possibly wider) table, and stays put on horizontal scroll. */
.table-wrap { container-type: inline-size; }
.detail-body { position: sticky; left: 8px; width: calc(100cqw - 16px); }
@media (prefers-reduced-motion: reduce) { .chevron { transition: none; } }
.source { display: inline-flex; align-items: center; gap: 6px; }
.peak { font-weight: 600; }
.keys { margin: 0; padding: 8px 12px; }
kbd { font-size: 11px; padding: 0 4px; border: 1px solid var(--border2); border-radius: var(--radius-sm); }
</style>
