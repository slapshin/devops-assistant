import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];
export type RuntimeConfig = Schemas["RuntimeConfig"];
export type AnalysisReport = Schemas["AnalysisReport"];
export type AnalysisJob = Schemas["AnalysisJob"];
export type AnalysisList = Schemas["AnalysisList"];
export type AnalysisSubmitted = Schemas["AnalysisSubmitted"];
export type ProjectList = Schemas["ProjectList"];
export type ProjectSummary = Schemas["ProjectSummary"];
export type ProjectInput = Schemas["ProjectInput"];
export type ReportSchedule = Schemas["ReportSchedule"];
export type Weekday = Schemas["Weekday"];
export type LabelMatcher = Schemas["LabelMatcher"];
export type ConnectionTest = Schemas["ConnectionTest"];
export type SourceInfo = Schemas["SourceInfo"];
export type SourceKind = Schemas["SourceKind"];
export type ConnectionTestRequest = Schemas["ConnectionTestRequest"];
export type Finding = Schemas["Finding"];
export type Evidence = Schemas["Evidence"];
export type DailyTrend = Schemas["DailyTrend"];
export type EpisodeSummary = Schemas["EpisodeSummary"];
export type SignalCoverage = Schemas["SignalCoverage"];
export type MetricCapability = Schemas["MetricCapability"];
export type Hypothesis = Schemas["Hypothesis"];
export type MetricSeries = Schemas["MetricSeries"];

/** Error carrying an RFC 9457 problem body, or a synthetic one for network failures. */
export class ApiError extends Error {
  readonly problem: Problem;
  /** Seconds from the Retry-After header, when the server sent one. */
  readonly retryAfter: number | null;

  constructor(problem: Problem, retryAfter: number | null = null) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.problem = problem;
    this.retryAfter = retryAfter;
  }
}

const PROBLEM_CONTENT_TYPE = "application/problem+json";
const NO_CONTENT = 204;

/** Problem body for failures where the server gave none (network error, non-problem response). */
function syntheticProblem(title: string, status: number): Problem {
  return { type: "about:blank", title, status, code: "internal_error" };
}

async function request<T>(method: string, path: `/api/${string}`, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(syntheticProblem("Lost connection to the assistant", 0));
  }

  if (!response.ok) {
    const contentType = response.headers.get("content-type") ?? "";
    const retryAfterSeconds = Number(response.headers.get("retry-after")) || null;
    if (contentType.includes(PROBLEM_CONTENT_TYPE)) {
      throw new ApiError((await response.json()) as Problem, retryAfterSeconds);
    }
    throw new ApiError(syntheticProblem(`Unexpected response (${response.status})`, response.status), retryAfterSeconds);
  }

  if (response.status === NO_CONTENT) return undefined as T;
  return (await response.json()) as T;
}

export const apiGet = <T>(path: `/api/${string}`) => request<T>("GET", path);
export const apiPost = <T>(path: `/api/${string}`, body: unknown) => request<T>("POST", path, body);
export const apiPut = <T>(path: `/api/${string}`, body: unknown) => request<T>("PUT", path, body);
export const apiDelete = <T>(path: `/api/${string}`) => request<T>("DELETE", path);
