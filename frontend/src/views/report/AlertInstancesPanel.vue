<script setup lang="ts">
import { computed, ref } from "vue";
import type { AlertInstanceActivity, Finding } from "../../api/client";
import { distinguishingLabels, instanceName } from "../../lib/alerts";
import { formatTime, utcTooltip } from "../../lib/format";

/** Instances listed before "Show all"; a noisy rule can have a hundred. */
const SHOWN = 10;
const URL_PATTERN = /^https?:\/\/\S+$/;

const props = defineProps<{ finding: Finding }>();

const showAll = ref(false);

const instances = computed(() => props.finding.alert_instances ?? []);
const keys = computed(() => distinguishingLabels(instances.value));
const shown = computed(() => (showAll.value ? instances.value : instances.value.slice(0, SHOWN)));
const activity = computed(() => (props.finding.signal === "alert_nodata" ? "in NoData or Error" : "firing"));

/** Annotations not already shown as the rule's summary in the finding details. */
function annotations(instance: AlertInstanceActivity): [string, string][] {
  const ruleSummary = props.finding.attributes?.alert_summary;
  return Object.entries(instance.annotations ?? {}).filter(([k, v]) => !(k === "summary" && v === ruleSummary));
}

function sortedLabels(instance: AlertInstanceActivity): [string, string][] {
  return Object.entries(instance.labels).sort(([a], [b]) => a.localeCompare(b));
}
</script>

<template>
  <section class="panel s24" aria-labelledby="instances-title">
    <div class="ph">
      <h2 id="instances-title">Alert instances</h2>
      <span class="count">{{ instances.length }}</span>
      <span class="sub">· {{ activity }} during this finding</span>
    </div>
    <div class="pb">
      <ul class="instances">
        <li v-for="(instance, i) in shown" :key="i" class="instance">
          <div class="head">
            <strong class="mono name">{{ instanceName(instance, keys) || "(single instance)" }}</strong>
            <span v-for="(a, j) in instance.active" :key="j" class="lbl">
              <span :title="utcTooltip(a.start)">{{ formatTime(a.start) }}</span>
              <template v-if="a.end"> – <span :title="utcTooltip(a.end)">{{ formatTime(a.end) }}</span></template>
              <template v-else> – still {{ activity }}</template>
            </span>
          </div>
          <dl v-if="annotations(instance).length" class="kv">
            <template v-for="[k, v] in annotations(instance)" :key="k">
              <dt>{{ k }}</dt>
              <dd class="annotation">
                <a v-if="URL_PATTERN.test(v)" :href="v" target="_blank" rel="noopener noreferrer">{{ v }}</a>
                <template v-else>{{ v }}</template>
              </dd>
            </template>
          </dl>
          <ul class="labels" :aria-label="`Labels of ${instanceName(instance, keys) || 'the instance'}`">
            <li v-for="[k, v] in sortedLabels(instance)" :key="k" class="tag mono" :class="{ key: keys.includes(k) }">{{ k }}={{ v }}</li>
          </ul>
        </li>
      </ul>
      <button v-if="instances.length > SHOWN" type="button" class="more" @click="showAll = !showAll">
        {{ showAll ? "Show fewer" : `Show all ${instances.length}` }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.instances { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.instance { display: flex; flex-direction: column; gap: 6px; padding: 8px 0; border-top: 1px solid var(--border); }
.instance:first-child { border-top: none; padding-top: 0; }
.head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
.name { font-size: 14px; color: var(--strong); overflow-wrap: anywhere; }
.annotation { white-space: pre-wrap; overflow-wrap: anywhere; }
.labels { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px; }
.labels .tag { white-space: normal; overflow-wrap: anywhere; color: var(--muted); }
.labels .tag.key { color: var(--strong); border-color: var(--strong); }
.more { align-self: flex-start; }
</style>
