<script setup lang="ts">
/** Source-kind glyphs: a flame for Prometheus-compatible metrics, a cloud for Cloudflare. */
defineProps<{ kind: "prometheus" | "cloudflare" }>();
</script>

<template>
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true" :class="['icon', kind]">
    <template v-if="kind === 'prometheus'">
      <path d="M8 1.5c.4 2.2 3.5 3.6 3.5 7a3.5 3.5 0 0 1-7 0c0-1.5.8-2.4 1.5-3 .1 1.2.6 1.9 1.3 2.2C7 6 7.4 3.6 8 1.5z" />
      <path d="M4 13.5h8" />
    </template>
    <path v-else d="M4.5 12.5a3 3 0 0 1-.4-6A4 4 0 0 1 11.8 6 3.25 3.25 0 0 1 11.5 12.5z" />
  </svg>
</template>

<style scoped>
.icon { flex-shrink: 0; }
.prometheus { color: #e6522c; }
.cloudflare { color: #f38020; }
</style>
