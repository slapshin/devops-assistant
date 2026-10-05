import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, type MaybeRefOrGetter, toValue } from "vue";
import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  type AnalysisJob,
  type AnalysisList,
  type AnalysisReport,
  type AnalysisSubmitted,
  type ConnectionTest,
  type ConnectionTestRequest,
  type ProjectInput,
  type ProjectList,
  type ProjectSummary,
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

const HEALTH_STALE_MS = 60_000;
const projectPath = (id: string) => `/api/projects/${encodeURIComponent(id)}` as const;

/** Polls while any project has a queued or running analysis, so inline progress stays live. */
export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => apiGet<ProjectList>("/api/projects"),
    refetchInterval: (query) => (query.state.data?.items.some((p) => p.active_analysis) ? JOB_POLL_INTERVAL_MS : false),
  });
}

export function useProject(id: MaybeRefOrGetter<string | null>) {
  return useQuery({
    queryKey: computed(() => ["project", toValue(id)]),
    queryFn: () => apiGet<ProjectSummary>(projectPath(toValue(id) ?? "")),
    enabled: computed(() => !!toValue(id)),
    refetchInterval: (query) => (query.state.data?.active_analysis ? JOB_POLL_INTERVAL_MS : false),
    retry: false,
  });
}

/** Lazy, cached connection test of a stored project (the server caches it for 60 s too). */
export function useProjectHealth(id: MaybeRefOrGetter<string>, enabled: MaybeRefOrGetter<boolean>) {
  return useQuery({
    queryKey: computed(() => ["project-health", toValue(id)]),
    queryFn: () => apiGet<ConnectionTest | null>(`${projectPath(toValue(id))}/health`),
    enabled: computed(() => toValue(enabled)),
    staleTime: HEALTH_STALE_MS,
    retry: false,
  });
}

function useProjectMutation<A, T>(fn: (args: A) => Promise<T>, removes?: (args: A) => string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (_data, args) => {
      // A deleted project's own queries must not refetch (they would 404).
      const gone = removes?.(args);
      if (gone) {
        client.removeQueries({ queryKey: ["project", gone] });
        client.removeQueries({ queryKey: ["project-health", gone] });
      }
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["project"] });
      void client.invalidateQueries({ queryKey: ["project-health"] });
    },
  });
}

export const useCreateProject = () =>
  useProjectMutation(({ body, cloneOf }: { body: ProjectInput; cloneOf?: string }) =>
    apiPost<ProjectSummary>(cloneOf ? `/api/projects?clone_of=${encodeURIComponent(cloneOf)}` : "/api/projects", body),
  );

export const useUpdateProject = () =>
  useProjectMutation(({ id, body }: { id: string; body: ProjectInput }) => apiPut<ProjectSummary>(projectPath(id), body));

export const useDeleteProject = () =>
  useProjectMutation(
    (id: string) => apiDelete<void>(projectPath(id)),
    (id) => id,
  );

export function useTestConnection() {
  return useMutation({
    mutationFn: (body: ConnectionTestRequest) => apiPost<ConnectionTest>("/api/projects/test-connection", body),
  });
}

export function useAnalyses(projectId: MaybeRefOrGetter<string | null>, limit = RECENT_ANALYSES_LIMIT) {
  return useInfiniteQuery({
    queryKey: computed(() => ["analyses", toValue(projectId), limit]),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ project_id: toValue(projectId) ?? "", limit: String(limit) });
      if (pageParam) params.set("cursor", pageParam);
      return apiGet<AnalysisList>(`/api/analyses?${params}`);
    },
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
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
      void client.invalidateQueries({ queryKey: ["project"] });
    },
  });
}

export function useCancel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete<AnalysisJob>(`/api/analyses/${encodeURIComponent(id)}`),
    onSuccess: (job) => {
      client.setQueryData(["analysis", job.analysis_id], job);
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["project"] });
    },
  });
}
