<script setup lang="ts">
import { THEME_CHOICES, theme } from "../lib/theme";
</script>

<template>
  <div class="tg" role="group" aria-label="Theme">
    <button
      v-for="c in THEME_CHOICES"
      :key="c.value"
      type="button"
      :aria-pressed="theme === c.value"
      @click="theme = c.value"
    >
      {{ c.label }}
    </button>
  </div>
</template>
