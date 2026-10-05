<script setup lang="ts">
/** Marks a route that exists in the UI spec but is implemented by a later task. */
import AppTopbar from "./shell/AppTopbar.vue";

defineProps<{ view: string; task: string }>();
</script>

<template>
  <AppTopbar :crumbs="[{ label: 'Projects', to: '/' }, { label: view }]" />
  <main class="page" aria-labelledby="ni-title">
    <section class="card notice">
      <h1 id="ni-title">{{ view }}</h1>
      <p>Not implemented yet — delivered by {{ task }}. See docs/UI_SPEC.md.</p>
      <slot />
    </section>
  </main>
</template>

<style scoped>
.notice { border-style: dashed; color: var(--muted); }
</style>
