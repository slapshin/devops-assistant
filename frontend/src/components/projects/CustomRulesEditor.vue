<script setup lang="ts">
/** Custom rules of a project's Prometheus source: list, edit one at a time, preview its query. */
import { computed, ref } from "vue";
import { ApiError, type CustomRulePreview, type CustomRulePreviewRequest } from "../../api/client";
import { usePreviewCustomRule } from "../../api/queries";
import {
  type CustomRuleDraft,
  MAX_CUSTOM_RULES,
  RULE_DIRECTIONS,
  RULE_MODES,
  RULE_SEVERITIES,
  RULE_UNITS,
  emptyRule,
  ruleHasErrors,
  ruleInput,
  ruleSummary,
  showsThreshold,
  slugify,
  usesBaseline,
} from "../../lib/customRules";
import { capitalize, formatValue } from "../../lib/format";
import type { FieldErrors } from "../../lib/projects";
import ValueSparkline from "./ValueSparkline.vue";

const rules = defineModel<CustomRuleDraft[]>({ required: true });
const props = defineProps<{
  errors: FieldErrors;
  /** The request without its rule; null while the source or labels are not usable yet. */
  previewBase: Omit<CustomRulePreviewRequest, "rule"> | null;
}>();

const previewer = usePreviewCustomRule();
const open = ref<number | null>(null);
const previews = ref<Record<number, { result: CustomRulePreview | null; error: string | null; signature: string }>>({});
const previewing = ref<number | null>(null);

const field = (i: number, name: string) => `prometheus.custom_rules.${i}.${name}`;
const error = (i: number, name: string) => props.errors[field(i, name)];
const errorId = (i: number, name: string) => `err-${field(i, name).replaceAll(".", "-")}`;
const describedBy = (i: number, name: string) => (error(i, name) ? errorId(i, name) : undefined);
/** A rule-level server error (e.g. thresholds that contradict the mode) has no field. */
const ruleError = (i: number) => props.errors[`prometheus.custom_rules.${i}`];

const signature = (r: CustomRuleDraft) => JSON.stringify([props.previewBase, ruleInput(r)]);
const summary = (r: CustomRuleDraft) => ruleSummary(ruleInput(r));
const canAdd = computed(() => rules.value.length < MAX_CUSTOM_RULES);

function add() {
  if (!canAdd.value) return;
  rules.value.push(emptyRule());
  open.value = rules.value.length - 1;
}

function remove(i: number) {
  rules.value.splice(i, 1);
  previews.value = {};
  if (open.value === i) open.value = null;
  else if (open.value !== null && open.value > i) open.value--;
}

function toggle(i: number) {
  open.value = open.value === i ? null : i;
}

/** The identifier follows the title until it is edited by hand. */
function setTitle(r: CustomRuleDraft, title: string) {
  const automatic = !r.slug || r.slug === slugify(r.title);
  r.title = title;
  if (automatic) r.slug = slugify(title);
}

async function preview(i: number) {
  const rule = rules.value[i];
  if (!rule || !props.previewBase) return;
  const sig = signature(rule);
  previewing.value = i;
  try {
    const result = await previewer.mutateAsync({ ...props.previewBase, rule: ruleInput(rule) });
    previews.value = { ...previews.value, [i]: { result, error: null, signature: sig } };
  } catch (e) {
    const message =
      e instanceof ApiError
        ? (e.problem.errors?.map((x) => x.message).join("; ") ?? e.problem.detail ?? e.problem.title)
        : "Preview failed";
    previews.value = { ...previews.value, [i]: { result: null, error: message, signature: sig } };
  } finally {
    previewing.value = null;
  }
}

const isStale = (i: number) => {
  const r = rules.value[i];
  const last = previews.value[i];
  return !!r && !!last && last.signature !== signature(r);
};
</script>

<template>
  <div class="rules stack">
    <p v-if="!rules.length" class="muted hint">No custom rules. The catalog's host, container, request, proxy, database, Node.js, Go and SeaweedFS signals are analysed either way.</p>
    <article v-for="(r, i) in rules" :key="i" class="rule" :class="{ invalid: ruleHasErrors(errors, i) }">
      <header class="rule-head">
        <div class="rule-name">
          <strong>{{ r.title.trim() || "Untitled rule" }}</strong>
          <code v-if="r.slug" class="slug">{{ r.slug }}</code>
          <span class="muted small">{{ summary(r) }}</span>
          <span v-if="ruleHasErrors(errors, i) && open !== i" class="field-error small">Needs attention</span>
        </div>
        <div class="row">
          <button type="button" :aria-expanded="open === i" :aria-controls="`rule-${i}-body`" @click="toggle(i)">
            {{ open === i ? "Done" : "Edit" }}
          </button>
          <button type="button" :aria-label="`Remove rule ${r.title || i + 1}`" @click="remove(i)">Remove</button>
        </div>
      </header>

      <div v-if="open === i" :id="`rule-${i}-body`" class="rule-body stack">
        <p v-if="ruleError(i)" class="field-error">{{ ruleError(i) }}</p>
        <div class="pair">
          <div class="field">
            <label :for="`rule-${i}-title`">Title</label>
            <input
              :id="`rule-${i}-title`"
              :value="r.title"
              maxlength="80"
              placeholder="Checkout failures"
              :aria-invalid="!!error(i, 'title')"
              :aria-describedby="describedBy(i, 'title')"
              @input="setTitle(r, ($event.target as HTMLInputElement).value)"
            >
            <p v-if="error(i, 'title')" :id="errorId(i, 'title')" class="field-error">{{ error(i, "title") }}</p>
          </div>
          <div class="field">
            <label :for="`rule-${i}-slug`">Identifier</label>
            <input
              :id="`rule-${i}-slug`"
              v-model="r.slug"
              class="mono"
              maxlength="40"
              autocomplete="off"
              :aria-invalid="!!error(i, 'slug')"
              :aria-describedby="describedBy(i, 'slug') ?? `rule-${i}-slug-hint`"
            >
            <p :id="`rule-${i}-slug-hint`" class="muted hint">Unique in the project; changing it starts a new history.</p>
            <p v-if="error(i, 'slug')" :id="errorId(i, 'slug')" class="field-error">{{ error(i, "slug") }}</p>
          </div>
        </div>
        <div class="field">
          <label :for="`rule-${i}-description`">What it means <span class="muted">(optional)</span></label>
          <textarea
            :id="`rule-${i}-description`"
            v-model="r.description"
            rows="2"
            maxlength="500"
            placeholder="Orders the payment provider rejected; a rise usually means…"
          />
          <p class="muted hint">Shown with findings and given to the AI explanation.</p>
        </div>
        <div class="field">
          <label :for="`rule-${i}-query`">PromQL query</label>
          <textarea
            :id="`rule-${i}-query`"
            v-model="r.query"
            class="mono"
            rows="3"
            spellcheck="false"
            placeholder="sum by (job) (rate(orders_failed_total[$__window]))"
            :aria-invalid="!!error(i, 'query')"
            :aria-describedby="describedBy(i, 'query') ?? `rule-${i}-query-hint`"
          />
          <p :id="`rule-${i}-query-hint`" class="muted hint">
            The project's labels are added to every selector. <code>$__window</code> is the 5-min rate window. Keep comparisons such
            as <code>&gt; 0.9</code> out of the query: set a threshold below instead.
          </p>
          <p v-if="error(i, 'query')" :id="errorId(i, 'query')" class="field-error">{{ error(i, "query") }}</p>
        </div>
        <div class="pair">
          <div class="field">
            <label :for="`rule-${i}-unit`">Unit</label>
            <select :id="`rule-${i}-unit`" v-model="r.unit">
              <option v-for="u in RULE_UNITS" :key="u.unit" :value="u.unit">{{ u.label }}</option>
            </select>
          </div>
          <div class="field">
            <label :for="`rule-${i}-mode`">Finding when</label>
            <select :id="`rule-${i}-mode`" v-model="r.mode" :aria-describedby="`rule-${i}-mode-hint`">
              <option v-for="m in RULE_MODES" :key="m.mode" :value="m.mode">{{ m.label }}</option>
            </select>
            <p :id="`rule-${i}-mode-hint`" class="muted hint">{{ RULE_MODES.find((m) => m.mode === r.mode)?.hint }}</p>
          </div>
        </div>
        <div v-if="usesBaseline(r.mode)" class="field">
          <label :for="`rule-${i}-direction`">Unusual when the value</label>
          <select :id="`rule-${i}-direction`" v-model="r.direction" class="narrow">
            <option v-for="d in RULE_DIRECTIONS" :key="d.direction" :value="d.direction">{{ d.label }}</option>
          </select>
        </div>
        <div v-if="showsThreshold(r.mode)" class="thresholds">
          <div class="field">
            <label :for="`rule-${i}-op`">Breached</label>
            <select :id="`rule-${i}-op`" v-model="r.thresholdOp">
              <option value="above">at or above</option>
              <option value="below">at or below</option>
            </select>
          </div>
          <div class="field">
            <label :for="`rule-${i}-threshold`">Threshold</label>
            <input
              :id="`rule-${i}-threshold`"
              v-model="r.threshold"
              class="mono"
              inputmode="decimal"
              :aria-invalid="!!error(i, 'threshold')"
              :aria-describedby="describedBy(i, 'threshold')"
            >
            <p v-if="error(i, 'threshold')" :id="errorId(i, 'threshold')" class="field-error">{{ error(i, "threshold") }}</p>
          </div>
          <div class="field">
            <label :for="`rule-${i}-critical`">Critical <span class="muted">(optional)</span></label>
            <input
              :id="`rule-${i}-critical`"
              v-model="r.criticalThreshold"
              class="mono"
              inputmode="decimal"
              :aria-invalid="!!error(i, 'critical_threshold')"
              :aria-describedby="describedBy(i, 'critical_threshold')"
            >
            <p v-if="error(i, 'critical_threshold')" :id="errorId(i, 'critical_threshold')" class="field-error">
              {{ error(i, "critical_threshold") }}
            </p>
          </div>
          <div class="field">
            <label :for="`rule-${i}-duration`">For at least (min)</label>
            <input
              :id="`rule-${i}-duration`"
              v-model.number="r.minDurationMinutes"
              type="number"
              min="5"
              max="1440"
              step="5"
              :aria-invalid="!!error(i, 'min_duration_minutes')"
              :aria-describedby="describedBy(i, 'min_duration_minutes')"
            >
            <p v-if="error(i, 'min_duration_minutes')" :id="errorId(i, 'min_duration_minutes')" class="field-error">
              {{ error(i, "min_duration_minutes") }}
            </p>
          </div>
        </div>
        <details>
          <summary>Advanced</summary>
          <div v-if="usesBaseline(r.mode)" class="pair">
            <div class="field">
              <label :for="`rule-${i}-factor`">Minimum change factor</label>
              <input
                :id="`rule-${i}-factor`"
                v-model="r.minChangeFactor"
                class="mono"
                inputmode="decimal"
                placeholder="1.5"
                :aria-invalid="!!error(i, 'min_change_factor')"
                :aria-describedby="describedBy(i, 'min_change_factor') ?? `rule-${i}-factor-hint`"
              >
              <p :id="`rule-${i}-factor-hint`" class="muted hint">An anomaly must be at least this many times the usual value (or below it divided by this).</p>
              <p v-if="error(i, 'min_change_factor')" :id="errorId(i, 'min_change_factor')" class="field-error">{{ error(i, "min_change_factor") }}</p>
            </div>
            <div class="field">
              <label :for="`rule-${i}-change`">Minimum change</label>
              <input
                :id="`rule-${i}-change`"
                v-model="r.minChange"
                class="mono"
                inputmode="decimal"
                :aria-invalid="!!error(i, 'min_change')"
                :aria-describedby="describedBy(i, 'min_change') ?? `rule-${i}-change-hint`"
              >
              <p :id="`rule-${i}-change-hint`" class="muted hint">An anomaly must differ from the usual value by at least this much, in the rule's unit.</p>
              <p v-if="error(i, 'min_change')" :id="errorId(i, 'min_change')" class="field-error">{{ error(i, "min_change") }}</p>
            </div>
          </div>
          <div class="pair">
            <div class="field">
              <label :for="`rule-${i}-severity`">{{ r.mode === "event" ? "Severity" : "Highest severity" }}</label>
              <select :id="`rule-${i}-severity`" v-model="r.maxSeverity">
                <option value="">{{ r.mode === "event" ? "High (default)" : "No limit" }}</option>
                <option v-for="s in RULE_SEVERITIES" :key="s" :value="s">{{ capitalize(s) }}</option>
              </select>
            </div>
            <div class="field">
              <label :for="`rule-${i}-labels`">Identity labels</label>
              <input
                :id="`rule-${i}-labels`"
                v-model="r.identityLabels"
                class="mono"
                autocomplete="off"
                placeholder="job, instance"
                :aria-invalid="!!error(i, 'identity_labels')"
                :aria-describedby="describedBy(i, 'identity_labels') ?? `rule-${i}-labels-hint`"
              >
              <p :id="`rule-${i}-labels-hint`" class="muted hint">Labels that tell series apart; empty uses every label the query returns.</p>
              <p v-if="error(i, 'identity_labels')" :id="errorId(i, 'identity_labels')" class="field-error">{{ error(i, "identity_labels") }}</p>
            </div>
          </div>
        </details>

        <div class="row">
          <button type="button" :disabled="!previewBase || !r.query.trim() || previewer.isPending.value" @click="preview(i)">
            {{ previewing === i ? "Running…" : "Preview last 24 h" }}
          </button>
          <span v-if="!previewBase" class="muted small">Enter the URL and labels first.</span>
        </div>
        <div v-if="previews[i]" class="preview stack" role="status" :class="{ stale: isStale(i) }">
          <p v-if="isStale(i)" class="muted small">Preview of an earlier version of this rule.</p>
          <p v-if="previews[i]?.error" class="field-error">{{ previews[i]?.error }}</p>
          <template v-else-if="previews[i]?.result">
            <p v-if="previews[i]?.result?.message" :class="previews[i]?.result?.ok ? 'muted small' : 'field-error'">
              {{ previews[i]?.result?.message }}
            </p>
            <pre v-if="previews[i]?.result?.query" class="code">{{ previews[i]?.result?.query }}</pre>
            <table v-if="previews[i]?.result?.series.length" class="gt">
              <caption class="sr-only">Series returned by {{ r.title || "the rule" }}</caption>
              <thead>
                <tr><th scope="col">Series</th><th scope="col" class="num">Latest</th><th scope="col">Last 24 h</th></tr>
              </thead>
              <tbody>
                <tr v-for="s in previews[i]?.result?.series" :key="s.entity.key">
                  <td class="mono">{{ s.entity.display_name }}</td>
                  <td class="num">{{ formatValue(s.latest, r.unit) }}</td>
                  <td>
                    <ValueSparkline
                      :values="s.values"
                      :threshold="showsThreshold(r.mode) && r.threshold.trim() !== '' ? Number(r.threshold) : null"
                      :label="`${s.entity.display_name} over the last 24 h`"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </template>
        </div>
      </div>
    </article>
    <p v-if="errors['prometheus.custom_rules']" class="field-error">{{ errors["prometheus.custom_rules"] }}</p>
    <div class="row">
      <button type="button" :disabled="!canAdd" @click="add">Add custom rule</button>
      <span class="muted small">{{ rules.length }} / {{ MAX_CUSTOM_RULES }}</span>
    </div>
  </div>
</template>

<style scoped>
.rule { border: 1px solid var(--border); border-radius: var(--radius); padding: var(--space); background: var(--canvas); }
.rule.invalid { border-color: var(--crit); }
.rule-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space); flex-wrap: wrap; }
.rule-name { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; min-width: 0; }
.slug { font-size: 0.8rem; }
.rule-body { margin-top: var(--space); }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.hint { font-size: 0.85rem; margin: 0; }
.small { font-size: 0.85rem; }
.pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space); align-items: start; }
.thresholds { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space); align-items: start; }
.narrow { max-width: 12rem; }
details summary { cursor: pointer; color: var(--muted); }
details > * + * { margin-top: var(--space); }
.preview.stale { opacity: 0.7; }
.preview .code { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 600px) {
  .pair, .thresholds { grid-template-columns: minmax(0, 1fr); }
}
</style>
