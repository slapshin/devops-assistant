import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, type MaybeRefOrGetter, toValue } from "vue";
import {
  apiDelete,
  apiGet,
  apiPost,
  type AnalysisJob,
  type AnalysisList,
  type AnalysisReport,
  type AnalysisSubmitted,
  type ProjectList,
  type RuntimeConfig,
} from "./client";

const ACTIVE_JOB_STATES = new Set(["queued", "running"]);
const JOB_POLL_INTERVAL_MS = 2000;
const JOB_FETCH_RETRY_COUNT = 3;
const RETRY_BASE_DELAY_MS = 1000;
const RETRY_MAX_DELAY_MS = 8000;
const RECENT_ANALYSES_LIMIT = 20;

export const isActive = (state: string) => ACTIVE_JOB_STATES.has(state);

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: () => apiGet<RuntimeConfig>("/api/config") });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => apiGet<ProjectList>("/api/projects") });
}

export function useAnalyses(projectId: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["analyses", toValue(projectId)]),
    queryFn: () => {
      const params = new URLSearchParams({ project_id: toValue(projectId) ?? "", limit: String(RECENT_ANALYSES_LIMIT) });
      return apiGet<AnalysisList>(`/api/analyses?${params}`);
    },
    enabled: computed(() => !!toValue(projectId)),
  });
}

export function useAnalysis(id: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => ["analysis", toValue(id)]),
    queryFn: () => apiGet<AnalysisJob>(`/api/analyses/${encodeURIComponent(toValue(id))}`),
    refetchInterval: (query) => (query.state.data && !isActive(query.state.data.state) ? false : JOB_POLL_INTERVAL_MS),
    refetchIntervalInBackground: true, // progress must not freeze in an unfocused tab
    retry: JOB_FETCH_RETRY_COUNT,
    retryDelay: (attempt) => Math.min(RETRY_MAX_DELAY_MS, RETRY_BASE_DELAY_MS * 2 ** attempt),
  });
}

export function useReport(id: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => ["report", toValue(id)]),
    queryFn: () => apiGet<AnalysisReport>(`/api/analyses/${encodeURIComponent(toValue(id))}/report`),
    staleTime: Infinity, // snapshots are immutable
    retry: false,
  });
}

export function useSubmit() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; end_time?: string }) =>
      apiPost<AnalysisSubmitted>("/api/analyses", body),
    onSuccess: (data) => {
      client.setQueryData(["analysis", data.analysis.analysis_id], data.analysis);
      void client.invalidateQueries({ queryKey: ["analyses"] });
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useCancel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete<AnalysisJob>(`/api/analyses/${encodeURIComponent(id)}`),
    onSuccess: (job) => client.setQueryData(["analysis", job.analysis_id], job),
  });
}
