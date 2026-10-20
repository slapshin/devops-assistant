<script setup lang="ts">
/** What each source's filter selected when the report was made, and which filter part removes data (T022). */
import { computed, ref } from "vue";
import type { FilterCheck } from "../../api/client";
import SourceIcon from "../../components/projects/SourceIcon.vue";
import { FIELD_LABELS, PROBLEM_VERDICTS, VERDICT_LABELS, checkVerdict, countText, labelText, sourceDiagnostics } from "../../lib/diagnostics";
import { sourceKindLabel } from "../../lib/projects";
import { useReportContext } from "./context";

/** Longer selections start collapsed so the filter checks of every source stay in view. */
const OPEN_SELECTION_MAX = 10;

const { report } = useReportContext();

/** Reports before 2.8 have no snapshots; a failed source is still shown with its error. */
const sources = computed(() => {
  const all = sourceDiagnostics(report.value);
  return all.some((s) => s.diagnostics || s.failures.length) ? all : [];
});
const copied = ref<string | null>(null);

const verdictClass = (check: FilterCheck) => {
  const verdict = checkVerdict(check);
  if (PROBLEM_VERDICTS.has(verdict)) return "v-critical";
  return verdict === "ok" ? "" : "muted";
};
/** A listed item (folder, project, agent, hostname) widens the selection, so dropping it is not counted. */
const withoutText = (check: FilterCheck) =>
  check.matched_without === null || check.matched_without === undefined ? "—" : countText(check.matched_without, check.unit);

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = text;
  } catch {
    copied.value = null;
  }
}
</script>

<template>
  <div class="dash">
    <section v-if="!sources.length" class="panel s24" aria-label="Scope">
      <p class="empty">This report predates scope diagnostics; run a new analysis to see what each source's filter selected.</p>
    </section>

    <section
      v-for="s in sources"
      :key="s.kind"
      class="panel s24"
      :aria-labelledby="`scope-${s.kind}`"
    >
      <div class="ph">
        <SourceIcon :kind="s.kind" />
        <h2 :id="`scope-${s.kind}`">{{ sourceKindLabel(s.kind) }}</h2>
        <span v-if="s.baseUrl" class="sub mono">· {{ s.baseUrl }}</span>
        <span v-if="s.diagnostics" class="sub">· {{ s.diagnostics.summary }}</span>
      </div>
      <div class="pb">
        <template v-if="s.failures.length">
          <p v-for="(m, i) in s.failures" :key="i" role="status" class="banner error">{{ m }}</p>
        </template>
        <p v-else-if="!s.diagnostics" class="empty">No scope diagnostics were recorded for this source.</p>

        <template v-if="s.diagnostics">
          <ul v-if="s.diagnostics.notes.length" class="notes">
            <li v-for="(n, i) in s.diagnostics.notes" :key="i"><code>{{ n.code }}</code> {{ n.message }}</li>
          </ul>

          <div v-if="s.diagnostics.checks.length" class="table-wrap">
            <table class="table">
              <caption class="sr-only">Filter checks of {{ sourceKindLabel(s.kind) }}</caption>
              <thead>
                <tr>
                  <th scope="col">Filter part</th>
                  <th scope="col" class="num">Matched</th>
                  <th scope="col" class="num">Without this part</th>
                  <th scope="col">Values in the source</th>
                  <th scope="col">Verdict</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in s.diagnostics.checks" :key="i">
                  <th scope="row"><span class="lbl field">{{ FIELD_LABELS[c.field] }}</span> <code>{{ c.part }}</code></th>
                  <td class="num">{{ countText(c.matched, c.unit) }}</td>
                  <td class="num">{{ withoutText(c) }}</td>
                  <td>
                    <span v-if="!c.available?.length" class="muted">—</span>
                    <span v-else class="chips">
                      <span v-for="v in c.available" :key="v" class="tag mono" :class="{ on: c.matching?.includes(v) }">{{ v }}</span>
                      <span v-if="c.available_truncated" class="lbl">and more</span>
                    </span>
                  </td>
                  <td :class="verdictClass(c)">{{ VERDICT_LABELS[checkVerdict(c)] }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <details :open="s.diagnostics.selection.length > 0 && s.diagnostics.selection.length <= OPEN_SELECTION_MAX">
            <summary>
              Selected
              <span class="lbl">· {{ s.diagnostics.selection.length < s.diagnostics.selection_total ? `showing ${s.diagnostics.selection.length} of ${s.diagnostics.selection_total}` : s.diagnostics.selection_total }}</span>
            </summary>
            <p v-if="!s.diagnostics.selection.length" class="muted">Nothing was selected.</p>
            <div v-else class="table-wrap">
              <table class="table">
                <caption class="sr-only">Selected by {{ sourceKindLabel(s.kind) }}</caption>
                <thead>
                  <tr><th scope="col">Name</th><th scope="col">Labels</th><th scope="col" class="num">Data</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in s.diagnostics.selection" :key="i">
                    <th scope="row">{{ e.display_name }}</th>
                    <td class="mono small">{{ labelText(e.labels) }}</td>
                    <td class="num">{{ countText(e.count, e.unit) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>

          <details v-if="s.diagnostics.samples.length">
            <summary>Samples</summary>
            <ul class="samples">
              <li v-for="(l, i) in s.diagnostics.samples" :key="i" class="mono small">{{ labelText(l) }}</li>
            </ul>
          </details>

          <details v-if="s.diagnostics.requests.length">
            <summary>Requests <span class="lbl">· {{ s.diagnostics.requests.length }}</span></summary>
            <div v-for="(q, i) in s.diagnostics.requests" :key="i" class="query">
              <pre class="code">{{ q }}</pre>
              <div class="row">
                <button type="button" @click="copy(q)">Copy</button>
                <span v-if="copied === q" role="status" class="lbl">Copied.</span>
              </div>
            </div>
          </details>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.empty { margin: 0; padding: 12px; }
.notes { margin: 0; padding-left: 20px; font-size: 13px; }
.table tbody th { font-weight: 400; }
.field { display: block; }
.chips { display: inline-flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.tag.on { border-color: var(--primary); color: var(--strong); font-weight: 500; }
.small { font-size: 12px; }
.samples { margin: 0; padding-left: 20px; }
details summary { cursor: pointer; font-size: 13px; font-weight: 500; color: var(--strong); }
details > * + * { margin-top: 8px; }
.query { display: flex; flex-direction: column; gap: 6px; }
</style>
