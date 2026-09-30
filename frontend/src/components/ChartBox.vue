<script setup lang="ts">
/** Lazily loads ECharts; the chart is decorative for assistive tech beyond its aria text,
 * and every chart has a "Show data" table alternative next to it. */
import { defineAsyncComponent } from "vue";

defineProps<{ option: Record<string, unknown>; label: string; height?: string }>();
const emit = defineEmits<{ click: [params: { dataIndex: number }] }>();
const VChart = defineAsyncComponent(() => import("./echarts"));
</script>

<template>
  <figure class="chart" :style="{ height: height ?? '280px' }">
    <VChart :option="option" autoresize :aria-label="label" role="img" @click="(p: { dataIndex: number }) => emit('click', p)" />
    <figcaption class="sr-only">{{ label }}</figcaption>
  </figure>
</template>

<style scoped>
.chart { margin: 0; width: 100%; }
</style>
