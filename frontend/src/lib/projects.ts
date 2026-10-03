import type { ConnectionTest, Problem, ProjectInput, ProjectSummary } from "../api/client";

/** Mirrors backend/app/domain/common.py and projects.py so most mistakes never reach the server. */
const LABEL_NAME = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const RESERVED_LABEL_PREFIX = "__";
export const MAX_MATCHERS = 10;
const MAX_NAME_CHARS = 100;
const MAX_LABEL_VALUE_CHARS = 256;
const SOURCE_PATH = "sources.0";

export type AuthType = "none" | "bearer" | "basic";

export interface MatcherDraft {
  name: string;
  value: string;
}

export interface ProjectDraft {
  name: string;
  description: string;
  matchers: MatcherDraft[];
  url: string;
  tlsVerify: boolean;
  authType: AuthType;
  token: string;
  username: string;
  password: string;
}

/** Which secret is already stored server-side, so an empty secret input keeps it. */
export interface StoredAuth {
  type: AuthType;
  secretSet: boolean;
}

export type FieldErrors = Record<string, string>;

export function emptyDraft(): ProjectDraft {
  return {
    name: "",
    description: "",
    matchers: [
      { name: "project", value: "" },
      { name: "env", value: "" },
    ],
    url: "",
    tlsVerify: true,
    authType: "none",
    token: "",
    username: "",
    password: "",
  };
}

export function draftFrom(project: ProjectSummary): ProjectDraft {
  const source = project.sources[0];
  const auth = source?.auth;
  return {
    name: project.name,
    description: project.description ?? "",
    matchers: project.matchers.map((m) => ({ ...m })),
    url: source?.url ?? "",
    tlsVerify: source?.tls_verify ?? true,
    authType: auth?.type ?? "none",
    token: "",
    username: auth?.type === "basic" ? auth.username : "",
    password: "",
  };
}

export function storedAuth(project: ProjectSummary | null | undefined): StoredAuth | null {
  const auth = project?.sources[0]?.auth;
  if (!auth) return null;
  if (auth.type === "bearer") return { type: "bearer", secretSet: auth.token_set };
  if (auth.type === "basic") return { type: "basic", secretSet: auth.password_set };
  return { type: "none", secretSet: false };
}

/** True when the empty secret input for the chosen auth type keeps a stored secret. */
export const keepsStoredSecret = (draft: ProjectDraft, stored: StoredAuth | null) =>
  !!stored && stored.type === draft.authType && stored.secretSet;

function urlError(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "Expected an http(s) URL such as http://localhost:8428";
  }
  if (url.protocol === "synthetic:") return url.host ? null : "Expected synthetic://<scenario>";
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname) return "Expected an http(s) URL such as http://localhost:8428";
  if (url.username || url.password) return "Must not contain credentials; use the authentication fields";
  if (url.search || url.hash) return "Must not contain a query string or fragment";
  return null;
}

export function validateMatchers(matchers: MatcherDraft[]): FieldErrors {
  const errors: FieldErrors = {};
  if (matchers.length === 0) errors.matchers = "Add at least one label";
  if (matchers.length > MAX_MATCHERS) errors.matchers = `At most ${MAX_MATCHERS} labels`;

  const seen = new Set<string>();
  matchers.forEach((m, i) => {
    const name = m.name.trim();
    if (!LABEL_NAME.test(name)) errors[`matchers.${i}.name`] = "Letters, digits and _; must not start with a digit";
    else if (name.startsWith(RESERVED_LABEL_PREFIX)) errors[`matchers.${i}.name`] = "Labels starting with __ are reserved";
    else if (seen.has(name)) errors[`matchers.${i}.name`] = "Duplicate label";
    seen.add(name);

    if (!m.value) errors[`matchers.${i}.value`] = "Required";
    else if (m.value.length > MAX_LABEL_VALUE_CHARS) errors[`matchers.${i}.value`] = `At most ${MAX_LABEL_VALUE_CHARS} characters`;
  });
  return errors;
}

export function validateSource(draft: ProjectDraft, stored: StoredAuth | null, required = false): FieldErrors {
  const errors: FieldErrors = {};
  const url = draft.url.trim();
  if (!url) {
    if (required) errors[`${SOURCE_PATH}.url`] = "Required to test the connection";
    return errors;
  }

  const bad = urlError(url);
  if (bad) errors[`${SOURCE_PATH}.url`] = bad;
  const keeps = keepsStoredSecret(draft, stored);
  if (draft.authType === "bearer" && !draft.token && !keeps) errors[`${SOURCE_PATH}.auth.token`] = "Required";
  if (draft.authType === "basic") {
    if (!draft.username.trim()) errors[`${SOURCE_PATH}.auth.username`] = "Required";
    if (!draft.password && !keeps) errors[`${SOURCE_PATH}.auth.password`] = "Required";
  }
  return errors;
}

export function validateDraft(draft: ProjectDraft, stored: StoredAuth | null): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = "Required";
  else if (name.length > MAX_NAME_CHARS) errors.name = `At most ${MAX_NAME_CHARS} characters`;
  return { ...errors, ...validateMatchers(draft.matchers), ...validateSource(draft, stored) };
}

function sourceInput(draft: ProjectDraft): NonNullable<ProjectInput["sources"]>[number] {
  const auth =
    draft.authType === "bearer"
      ? { type: "bearer" as const, ...(draft.token ? { token: draft.token } : {}) }
      : draft.authType === "basic"
        ? { type: "basic" as const, username: draft.username.trim(), ...(draft.password ? { password: draft.password } : {}) }
        : { type: "none" as const };
  return { kind: "prometheus", url: draft.url.trim(), tls_verify: draft.tlsVerify, auth };
}

export const matchersInput = (draft: ProjectDraft) => draft.matchers.map((m) => ({ name: m.name.trim(), value: m.value }));

export function toInput(draft: ProjectDraft): ProjectInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    matchers: matchersInput(draft),
    sources: draft.url.trim() ? [sourceInput(draft)] : [],
  };
}

export const testRequestSource = sourceInput;

/** Field errors from a 422 problem, keyed like validateDraft ("matchers.0.name", "sources.0.url"). */
export function serverFieldErrors(problem: Problem): FieldErrors {
  const errors: FieldErrors = {};
  for (const e of problem.errors ?? []) {
    const field = (e.field ?? "").replace(/^body\./, "").replace(/^source\./, `${SOURCE_PATH}.`);
    errors[field] ??= e.message ?? "Invalid";
  }
  return errors;
}

/** Host, port and path prefix of a source URL; "synthetic: <scenario>" for demo sources. */
export function sourceHost(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "synthetic:") return `synthetic: ${parsed.host}`;
    return `${parsed.host}${parsed.pathname === "/" ? "" : parsed.pathname}`;
  } catch {
    return url;
  }
}

export type HealthKey = "ok" | "checking" | "unreachable" | "auth" | "no_series" | "not_configured" | "unreadable" | "error";

export const HEALTH_LABELS: Record<HealthKey, { label: string; icon: string }> = {
  ok: { label: "Reachable", icon: "✓" },
  checking: { label: "Checking…", icon: "◌" },
  unreachable: { label: "Unreachable", icon: "!" },
  auth: { label: "Auth failed", icon: "!" },
  no_series: { label: "No matching series", icon: "◐" },
  not_configured: { label: "No source", icon: "–" },
  unreadable: { label: "Credentials unreadable", icon: "!" },
  error: { label: "Check failed", icon: "!" },
};

export function healthKey(
  project: ProjectSummary,
  health: ConnectionTest | null | undefined,
  state: { pending: boolean; failed: boolean },
): HealthKey {
  if (project.sources.length === 0) return "not_configured";
  if (!project.credentials_readable) return "unreadable";
  if (state.failed) return "error";
  if (state.pending || health === undefined) return "checking";
  if (health === null) return "not_configured";
  return testKey(health);
}

export function testKey(test: ConnectionTest): HealthKey {
  if (!test.reachable) return "unreachable";
  if (test.auth_ok === false) return "auth";
  if (test.matched_series === 0) return "no_series";
  return "ok";
}
