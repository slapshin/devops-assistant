import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];
export type RuntimeConfig = Schemas["RuntimeConfig"];
export type AnalysisReport = Schemas["AnalysisReport"];
export type AnalysisJob = Schemas["AnalysisJob"];

/** Error carrying an RFC 9457 problem body, or a synthetic one for network failures. */
export class ApiError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.problem = problem;
  }
}

export async function apiGet<T>(path: `/api/${string}`, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { Accept: "application/json", ...init?.headers } });
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
    if (type.includes("application/problem+json")) {
      throw new ApiError((await res.json()) as Problem);
    }
    throw new ApiError({
      type: "about:blank",
      title: `Unexpected response (${res.status})`,
      status: res.status,
      code: "internal_error",
    });
  }
  return (await res.json()) as T;
}
