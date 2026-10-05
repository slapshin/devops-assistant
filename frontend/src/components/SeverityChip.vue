<script setup lang="ts">
import { capitalize } from "../lib/format";

defineProps<{ severity: string }>();
</script>

<template>
  <span class="sev" :class="`sev-${severity}`">{{ capitalize(severity) }}</span>
</template>
