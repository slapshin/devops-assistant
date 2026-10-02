<script setup lang="ts">
import ThemeSwitch from "./components/ThemeSwitch.vue";
</script>

<template>
  <header class="header">
    <RouterLink to="/" class="brand">DevOps AI Assistant</RouterLink>
    <ThemeSwitch />
  </header>
  <main class="main">
    <RouterView />
  </main>
</template>

<style scoped>
.header {
  display: flex;
  gap: calc(var(--space) * 2);
  align-items: center;
  justify-content: space-between;
  padding: var(--space) calc(var(--space) * 4);
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.brand {
  font-weight: 700;
  color: inherit;
  text-decoration: none;
}

.main {
  padding: calc(var(--space) * 3) calc(var(--space) * 4) calc(var(--space) * 6);
  max-width: 1320px;
  margin: 0 auto;
}

@media (max-width: 600px) {
  .header { padding: var(--space) calc(var(--space) * 2); }
  .main { padding: calc(var(--space) * 2); }
}
</style>
