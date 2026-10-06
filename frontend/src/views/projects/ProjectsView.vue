<script setup lang="ts">
import { computed, ref } from "vue";
import { useProjects } from "../../api/queries";
import ProjectCard from "../../components/projects/ProjectCard.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";

/** The text filter appears once the list is long enough to need one. */
const FILTER_THRESHOLD = 10;

const projects = useProjects();
const filter = ref("");

const items = computed(() => projects.data.value?.items ?? []);
const visible = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  if (!needle) return items.value;
  return items.value.filter(
    (p) =>
      p.name.toLowerCase().includes(needle) ||
      p.matchers.some((m) => `${m.name}=${m.value}`.toLowerCase().includes(needle)),
  );
});
</script>

<template>
  <AppTopbar :crumbs="[{ label: 'Projects' }]" />
  <main class="page" aria-labelledby="projects-title">
    <div class="page-head">
      <h1 id="projects-title">Projects</h1>
      <RouterLink to="/projects/new" class="button primary">New project</RouterLink>
    </div>
    <p class="muted">Each project analyses the latest 24 hours of its own series against up to 14 preceding days.</p>

    <div v-if="projects.isPending.value" aria-busy="true">
      <div class="skeleton" style="height: 88px" /><div class="skeleton" style="height: 88px" />
    </div>
    <div v-else-if="projects.isError.value" role="alert" class="banner error">
      <strong>Projects could not be loaded.</strong>
      {{ projects.error.value?.message }}
      <button type="button" @click="projects.refetch()">Retry</button>
    </div>
    <div v-else-if="items.length === 0" class="card empty stack">
      <h2>No projects yet</h2>
      <p>
        A project is analysed from its own data sources: Prometheus-compatible metrics selected by labels such as
        <code>project="shop"</code> and <code>env="prod"</code>, a Cloudflare zone's edge traffic and security events, a Sentry project's errors and transactions, and Wazuh agents' security alerts.
      </p>
      <RouterLink to="/projects/new" class="button primary">Create project</RouterLink>
    </div>
    <template v-else>
      <label v-if="items.length > FILTER_THRESHOLD" class="filter">
        <span class="sr-only">Filter projects</span>
        <input v-model="filter" type="search" placeholder="Filter by name or label">
      </label>
      <p v-if="visible.length === 0" class="muted">No project matches “{{ filter }}”.</p>
      <ul class="projects">
        <li v-for="p in visible" :key="p.project_id"><ProjectCard :project="p" /></li>
      </ul>
    </template>
  </main>
</template>

<style scoped>
.projects { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space); }
.filter input { width: min(100%, 360px); }
.empty h2 { margin-top: 0; }
</style>
