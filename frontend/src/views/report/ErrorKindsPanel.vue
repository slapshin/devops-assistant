<script setup lang="ts">
import { computed } from "vue";
import type { Finding } from "../../api/client";
import { issueLink } from "../../lib/errors";
import { useReportContext } from "./context";

const props = defineProps<{ finding: Finding }>();

const { report } = useReportContext();

const kinds = computed(() => props.finding.error_kinds ?? []);
const total = computed(() => kinds.value.reduce((sum, k) => sum + k.events, 0));
const links = computed(() => kinds.value.map((k) => issueLink(k, props.finding, report.value.sources ?? [])));

function share(events: number): string {
  return total.value ? `${Math.round((events / total.value) * 100)}%` : "";
}
</script>

<template>
  <section class="panel s24" aria-labelledby="error-kinds-title">
    <div class="ph">
      <h2 id="error-kinds-title">Errors</h2>
      <span class="count">{{ kinds.length }}</span>
      <span class="sub">· error events of each kind during this finding</span>
    </div>
    <div class="pb">
      <table class="gt kinds">
        <thead>
          <tr>
            <th scope="col">Error</th>
            <th scope="col">Issue</th>
            <th scope="col" class="num">Events</th>
            <th scope="col" class="num">Share</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(k, i) in kinds" :key="k.issue">
            <td class="title">
              <template v-if="k.title">{{ k.title }}</template>
              <span v-else class="muted">Other errors (not among the project's top issues)</span>
            </td>
            <td class="mono">
              <a v-if="links[i]" :href="links[i]" target="_blank" rel="noopener noreferrer">{{ k.issue }}</a>
              <span v-else-if="k.title">{{ k.issue }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td class="num">{{ k.events.toLocaleString() }}</td>
            <td class="num">{{ share(k.events) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.kinds .title { white-space: normal; overflow-wrap: anywhere; color: var(--strong); padding-top: 6px; padding-bottom: 6px; }
</style>
