<script setup lang="ts">
/** The full state timeline: every finding of the latest day, narrowed to the chosen sources. */
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { SourceKind } from "../../api/client";
import IncidentTimeline from "../../components/IncidentTimeline.vue";
import SourceIcon from "../../components/projects/SourceIcon.vue";
import { sourceByFamily } from "../../lib/findings";
import { sourceKindLabel } from "../../lib/projects";
import { useReportContext } from "./context";

/** `?source=` lists the shown sources, comma separated; without it every source is shown. */
const SOURCE_SEPARATOR = ",";

const route = useRoute();
const router = useRouter();
const { report } = useReportContext();

const sourceOf = computed(() => sourceByFamily(report.value.capabilities));
/** Only sources that produced findings, so every toggle narrows to something. */
const sources = computed(() => {
  const counts = new Map<SourceKind, number>();
  for (const f of report.value.findings) {
    const kind = sourceOf.value.get(f.family);
    if (kind) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  return [...counts].map(([kind, count]) => ({ kind, count }));
});
const selected = computed<Set<string>>(() => {
  const raw = route.query.source;
  if (typeof raw !== "string") return new Set(sources.value.map((s) => s.kind));
  return new Set(raw.split(SOURCE_SEPARATOR).filter(Boolean));
});
const allSelected = computed(() => sources.value.every((s) => selected.value.has(s.kind)));
const filtered = computed(() =>
  allSelected.value ? report.value.findings : report.value.findings.filter((f) => selected.value.has(sourceOf.value.get(f.family) ?? "")),
);
const link = (id: string) => `/reports/${report.value.analysis_id}/findings/${id}`;

function setSources(kinds: Set<string>) {
  const all = sources.value.every((s) => kinds.has(s.kind));
  const source = all ? undefined : sources.value.filter((s) => kinds.has(s.kind)).map((s) => s.kind).join(SOURCE_SEPARATOR);
  void router.replace({ query: { ...route.query, source } });
}

function toggle(kind: SourceKind) {
  const next = new Set(selected.value);
  if (!next.delete(kind)) next.add(kind);
  setSources(next);
}

const showAll = () => setSources(new Set(sources.value.map((s) => s.kind)));
</script>

<template>
  <div class="dash">
    <section v-if="!report.findings.length" class="panel s24" aria-label="State timeline">
      <p class="empty">No findings in the latest 24 hours, so there are no episodes to place on the timeline.</p>
    </section>
    <template v-else>
      <div class="controls s24">
        <div class="tg" role="group" aria-label="Sources">
          <button type="button" :aria-pressed="allSelected" @click="showAll">All · {{ report.findings.length }}</button>
          <button v-for="s in sources" :key="s.kind" type="button" :aria-pressed="selected.has(s.kind)" @click="toggle(s.kind)">
            <SourceIcon :kind="s.kind" />{{ sourceKindLabel(s.kind) }} · {{ s.count }}
          </button>
        </div>
      </div>
      <IncidentTimeline
        v-if="filtered.length"
        class="s24"
        :findings="filtered"
        :start="report.windows.latest_day.start"
        :end="report.windows.latest_day.end"
        :link="link"
        :max-lanes="filtered.length"
        :source-of="sourceOf"
      />
      <section v-else class="panel s24" aria-label="State timeline">
        <div class="empty row">
          No sources selected.
          <button type="button" @click="showAll">Show all sources</button>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.tg button { display: inline-flex; align-items: center; gap: 6px; }
.empty { margin: 0; padding: 12px; }
</style>
