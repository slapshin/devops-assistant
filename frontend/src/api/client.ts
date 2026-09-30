import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];
export type RuntimeConfig = Schemas["RuntimeConfig"];
export type AnalysisReport = Schemas["AnalysisReport"];
export type AnalysisJob = Schemas["AnalysisJob"];
export type AnalysisList = Schemas["AnalysisList"];
export type AnalysisSubmitted = Schemas["AnalysisSubmitted"];
export type ProjectList = Schemas["ProjectList"];
export type EnvList = Schemas["EnvList"];
export type Finding = Schemas["Finding"];
export type Evidence = Schemas["Evidence"];
export type DailyTrend = Schemas["DailyTrend"];
export type EpisodeSummary = Schemas["EpisodeSummary"];
export type SignalCoverage = Schemas["SignalCoverage"];
export type Hypothesis = Schemas["Hypothesis"];
export type MetricSeries = Schemas["MetricSeries"];

/** Error carrying an RFC 9457 problem body, or a synthetic one for network failures. */
export class ApiError extends Error {
  readonly problem: Problem;
  readonly retryAfter: number | null;

  constructor(problem: Problem, retryAfter: number | null = null) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.problem = problem;
    this.retryAfter = retryAfter;
  }
}

async function request<T>(method: string, path: `/api/${string}`, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError({
      type: "about:blank",
      title: "Lost connection to the assistant",
      status: 0,
      code: "internal_error",
    });
  }
  if (!res.ok) {
    const type = res.headers.get("content-type") ?? "";
    const retry = Number(res.headers.get("retry-after")) || null;
    if (type.includes("application/problem+json")) {
      throw new ApiError((await res.json()) as Problem, retry);
    }
    throw new ApiError(
      { type: "about:blank", title: `Unexpected response (${res.status})`, status: res.status, code: "internal_error" },
      retry,
    );
  }
  return (await res.json()) as T;
}

export const apiGet = <T>(path: `/api/${string}`) => request<T>("GET", path);
export const apiPost = <T>(path: `/api/${string}`, body: unknown) => request<T>("POST", path, body);
export const apiDelete = <T>(path: `/api/${string}`) => request<T>("DELETE", path);
