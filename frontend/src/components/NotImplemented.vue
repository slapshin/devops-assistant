<script setup lang="ts">
/** Marks a route that exists in the UI spec but is implemented by a later task. */
defineProps<{ view: string; task: string }>();
</script>

<template>
  <section aria-labelledby="ni-title" class="notice">
    <h1 id="ni-title">{{ view }}</h1>
    <p>Not implemented yet — delivered by {{ task }}. See docs/UI_SPEC.md.</p>
    <slot />
  </section>
</template>

<style scoped>
.notice {
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: calc(var(--space) * 2);
  color: var(--text-muted);
}
</style>
