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
  type EnvList,
  type DiscoveredProjectList,
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
  return useQuery({ queryKey: ["discovered-projects"], queryFn: () => apiGet<DiscoveredProjectList>("/api/discovery/projects") });
}

/** Keyed by project, so a late response for a previous project can never fill the list. */
export function useEnvs(project: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["envs", toValue(project)]),
    queryFn: () => apiGet<EnvList>(`/api/discovery/projects/${encodeURIComponent(toValue(project) ?? "")}/envs`),
    enabled: computed(() => !!toValue(project)),
  });
}

export function useAnalyses(project: MaybeRefOrGetter<string | null>, env: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["analyses", toValue(project), toValue(env)]),
    queryFn: () => {
      const params = new URLSearchParams({
        project: toValue(project) ?? "",
        env: toValue(env) ?? "",
        limit: String(RECENT_ANALYSES_LIMIT),
      });
      return apiGet<AnalysisList>(`/api/analyses?${params}`);
    },
    enabled: computed(() => !!toValue(project) && !!toValue(env)),
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
    mutationFn: (body: { project: string; env: string; end_time?: string }) =>
      apiPost<AnalysisSubmitted>("/api/analyses", body),
    onSuccess: (data) => {
      client.setQueryData(["analysis", data.analysis.analysis_id], data.analysis);
      void client.invalidateQueries({ queryKey: ["analyses"] });
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
