<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { apiGet, type RuntimeConfig } from "../api/client";
import NotImplemented from "../components/NotImplemented.vue";

const config = useQuery({
  queryKey: ["config"],
  queryFn: () => apiGet<RuntimeConfig>("/api/config"),
});
</script>

<template>
  <NotImplemented view="Start" task="T008 (scope selection, Analyze, recent reports)">
    <h2>Backend connection</h2>
    <p v-if="config.isPending.value">Loading configuration…</p>
    <p v-else-if="config.isError.value" role="alert">
      Backend unavailable: {{ config.error.value?.message }}
      <button type="button" @click="config.refetch()">Retry</button>
    </p>
    <dl v-else-if="config.data.value">
      <dt>Metrics source</dt>
      <dd>{{ config.data.value.metrics_source }}</dd>
      <dt>Detector</dt>
      <dd>{{ config.data.value.detector_version }} ({{ config.data.value.config_hash }})</dd>
      <dt>AI explanations</dt>
      <dd>{{ config.data.value.ai_provider }} · {{ config.data.value.explanation_status }}</dd>
    </dl>
  </NotImplemented>
</template>
