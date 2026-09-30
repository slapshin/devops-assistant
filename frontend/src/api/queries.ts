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
  type ProjectList,
  type RuntimeConfig,
} from "./client";

const ACTIVE = new Set(["queued", "running"]);
export const POLL_MS = 2000;

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: () => apiGet<RuntimeConfig>("/api/config") });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => apiGet<ProjectList>("/api/projects") });
}

/** Keyed by project, so a late response for a previous project can never fill the list. */
export function useEnvs(project: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["envs", toValue(project)]),
    queryFn: () => apiGet<EnvList>(`/api/projects/${encodeURIComponent(toValue(project) ?? "")}/envs`),
    enabled: computed(() => !!toValue(project)),
  });
}

export function useAnalyses(project: MaybeRefOrGetter<string | null>, env: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["analyses", toValue(project), toValue(env)]),
    queryFn: () => {
      const p = new URLSearchParams({ project: toValue(project) ?? "", env: toValue(env) ?? "", limit: "20" });
      return apiGet<AnalysisList>(`/api/analyses?${p}`);
    },
    enabled: computed(() => !!toValue(project) && !!toValue(env)),
  });
}

export function useAnalysis(id: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => ["analysis", toValue(id)]),
    queryFn: () => apiGet<AnalysisJob>(`/api/analyses/${encodeURIComponent(toValue(id))}`),
    refetchInterval: (query) => (query.state.data && !ACTIVE.has(query.state.data.state) ? false : POLL_MS),
    refetchIntervalInBackground: true, // progress must not freeze in an unfocused tab
    retry: 3,
    retryDelay: (attempt) => Math.min(8000, 1000 * 2 ** attempt),
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

export const isActive = (state: string) => ACTIVE.has(state);
