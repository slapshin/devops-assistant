<script setup lang="ts">
import { ref } from "vue";

defineProps<{ columns: string[]; rows: (string | number)[][]; caption: string }>();
const open = ref(false);
</script>

<template>
  <div class="data">
    <button type="button" class="link" :aria-expanded="open" @click="open = !open">
      {{ open ? "Hide data" : "Show data" }}
    </button>
    <div v-if="open" class="scroll" tabindex="0" :aria-label="caption">
      <table>
        <caption class="sr-only">{{ caption }}</caption>
        <thead>
          <tr><th v-for="c in columns" :key="c" scope="col">{{ c }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="i"><td v-for="(v, j) in r" :key="j">{{ v }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.data { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.scroll { align-self: stretch; max-height: 320px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); }
table { border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 4px 10px; text-align: left; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { position: sticky; top: 0; background: var(--panel); color: var(--strong); font-weight: 500; border-bottom-color: var(--border); }
</style>
