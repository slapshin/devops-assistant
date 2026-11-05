<script setup lang="ts">
import AppRail from "./components/shell/AppRail.vue";
</script>

<template>
  <div class="shell">
    <AppRail />
    <div class="content">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.shell { display: flex; flex-wrap: wrap; min-height: 100vh; }
.content { flex: 999 1 560px; min-width: 0; display: flex; flex-direction: column; }
</style>
