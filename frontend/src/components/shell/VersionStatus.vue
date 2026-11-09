<script setup lang="ts">
/**
 * Running version and commit at the foot of the rail, and whether it is the latest build
 * (the head of the tracked GitHub branch). Collapsed, only the status dot shows; its tooltip has the rest.
 */
import { computed } from "vue";
import type { VersionInfo } from "../../api/client";
import { useVersion } from "../../api/queries";
import { railCollapsed } from "../../lib/rail";

const SHORT_COMMIT = 7;

const { data } = useVersion();

function statusText(info: VersionInfo): string {
  const { state, branch, commits_behind: behind, reason } = info.update;
  switch (state) {
    case "latest":
      return "Latest";
    case "outdated":
      return behind ? `Update available · ${behind} commit${behind === 1 ? "" : "s"} behind` : "Update available";
    case "ahead":
      return `Ahead of ${branch}`;
    case "disabled":
      return "Update check off";
    default:
      return reason ? `Update status unknown: ${reason}` : "Update status unknown";
  }
}

const label = computed(() => {
  const info = data.value;
  if (!info) return null;
  const commit = info.commit ? ` · ${info.commit.slice(0, SHORT_COMMIT)}` : "";
  return `v${info.version}${commit}`;
});
const status = computed(() => (data.value ? statusText(data.value) : ""));
const shortStatus = computed(() => (data.value?.update.state === "unknown" ? "Update status unknown" : status.value));
const tooltip = computed(() => {
  const info = data.value;
  if (!info) return undefined;
  const built = info.built_at ? `\nBuilt ${info.built_at.slice(0, 10)}` : "";
  return `${label.value}${built}\n${status.value}`;
});
const link = computed(() => data.value?.update.compare_url ?? data.value?.repository_url);
</script>

<template>
  <a
    v-if="data && label"
    class="version"
    :class="[data.update.state, { collapsed: railCollapsed }]"
    :href="link"
    target="_blank"
    rel="noopener noreferrer"
    :title="tooltip"
    :aria-label="`${label}. ${status}`"
  >
    <span class="dot" aria-hidden="true" />
    <span class="text">
      <span class="id">{{ label }}</span>
      <span class="state">{{ shortStatus }}</span>
    </span>
  </a>
</template>

<style scoped>
.version { --state: var(--muted); display: flex; align-items: center; gap: 10px; padding: 6px 11px; border-radius: var(--radius); color: var(--muted); text-decoration: none; font-size: 12px; line-height: 1.35; }
.version:hover { background: var(--hover); }
.version.latest { --state: var(--ok); }
.version.outdated { --state: var(--high); }
.version.ahead { --state: var(--info); }
.dot { flex: 0 0 auto; width: 8px; height: 8px; margin: 0 5px; border-radius: 50%; background: var(--state); }
.text { display: flex; flex-direction: column; min-width: 0; }
.id { font-family: var(--font-mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.state { color: var(--state); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.version.outdated .state { font-weight: 600; }
.version.collapsed { width: 40px; height: 32px; padding: 0; justify-content: center; }
.version.collapsed .text { display: none; }

@media (max-width: 700px) {
  .version { display: none; }
}
</style>
