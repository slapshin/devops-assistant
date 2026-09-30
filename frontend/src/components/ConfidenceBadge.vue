<script setup lang="ts">
defineProps<{ confidence: string }>();
</script>

<template>
  <span class="conf">Confidence: {{ confidence }}</span>
</template>

<style scoped>
.conf {
  font-size: 0.8rem;
  color: var(--text-muted);
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: 0 calc(var(--space) * 0.5);
  white-space: nowrap;
}
</style>
