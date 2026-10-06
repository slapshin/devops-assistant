import type { AnalysisReport, ConnectionTest, Problem, ProjectInput, ProjectSummary, ReportSchedule, SourceInfo, SourceKind, Weekday } from "../api/client";

/** Mirrors backend/app/domain/common.py and projects.py so most mistakes never reach the server. */
const LABEL_NAME = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const RESERVED_LABEL_PREFIX = "__";
export const MAX_MATCHERS = 10;
const MAX_NAME_CHARS = 100;
const MAX_LABEL_VALUE_CHARS = 256;
/** Mirrors backend/app/domain/projects.py (Cloudflare). */
export const CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql";
const ZONE_ID = /^[0-9a-f]{32}$/;
const HOSTNAME = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
export const MAX_HOSTNAMES = 20;
/** Mirrors backend/app/domain/projects.py (Sentry). */
export const SENTRY_URL = "https://sentry.io";
const SENTRY_SLUG = /^[a-z0-9][a-z0-9_-]{0,99}$/;
const SENTRY_ENVIRONMENT = /^[^\s/]{1,64}$/;
const SENTRY_TAG_KEY = /^[a-zA-Z0-9_.:-]{1,32}$/;
export const MAX_SENTRY_PROJECTS = 10;
export const MAX_SENTRY_TAGS = 10;
const MAX_SENTRY_TAG_VALUE_CHARS = 200;

export type AuthType = "none" | "bearer" | "basic";

/** Mirrors backend/app/domain/schedule.py. */
const LOCAL_TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
export const WEEKDAYS: { day: Weekday; label: string }[] = [
  { day: "mon", label: "Mon" },
  { day: "tue", label: "Tue" },
  { day: "wed", label: "Wed" },
  { day: "thu", label: "Thu" },
  { day: "fri", label: "Fri" },
  { day: "sat", label: "Sat" },
  { day: "sun", label: "Sun" },
];
const ALL_DAYS = WEEKDAYS.map((w) => w.day);
const WORK_DAYS: Weekday[] = ["mon", "tue", "wed", "thu", "fri"];
const DEFAULT_SCHEDULE_TIME = "08:00";
/** Mirrors MAX_KEEP_REPORTS in backend/app/domain/projects.py. */
export const MAX_KEEP_REPORTS = 1000;
const DEFAULT_KEEP_REPORTS = 30;

/** The viewer's IANA zone, so "08:00" means their morning by default. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** IANA zones the browser knows, for suggestions; the server validates the final value. */
export function timezoneOptions(): string[] {
  try {
    return ["UTC", ...Intl.supportedValuesOf("timeZone").filter((z) => z !== "UTC")];
  } catch {
    return ["UTC"];
  }
}

export interface MatcherDraft {
  name: string;
  value: string;
}

export interface TagDraft {
  key: string;
  value: string;
}

export interface ProjectDraft {
  name: string;
  description: string;
  matchers: MatcherDraft[];
  /** Prometheus source; an empty URL means none. */
  url: string;
  tlsVerify: boolean;
  authType: AuthType;
  token: string;
  username: string;
  password: string;
  cloudflare: boolean;
  zoneId: string;
  /** Comma- or space-separated; empty analyses the whole zone. */
  hostnames: string;
  cfToken: string;
  cfApiUrl: string;
  sentry: boolean;
  sentryOrg: string;
  /** Comma- or space-separated project slugs. */
  sentryProjects: string;
  /** Tag filters for every project; rows left completely empty are ignored. */
  sentryTags: TagDraft[];
  /** Empty analyses every environment. */
  sentryEnvironment: string;
  sentryToken: string;
  sentryApiUrl: string;
  sentryTlsVerify: boolean;
  scheduled: boolean;
  scheduleTime: string;
  scheduleTimezone: string;
  scheduleWeekdays: Weekday[];
  limitReports: boolean;
  keepReports: number;
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
    cloudflare: false,
    zoneId: "",
    hostnames: "",
    cfToken: "",
    cfApiUrl: CLOUDFLARE_GRAPHQL_URL,
    sentry: false,
    sentryOrg: "",
    sentryProjects: "",
    sentryTags: [],
    sentryEnvironment: "",
    sentryToken: "",
    sentryApiUrl: SENTRY_URL,
    sentryTlsVerify: true,
    scheduled: false,
    scheduleTime: DEFAULT_SCHEDULE_TIME,
    scheduleTimezone: browserTimezone(),
    scheduleWeekdays: [...ALL_DAYS],
    limitReports: false,
    keepReports: DEFAULT_KEEP_REPORTS,
  };
}

type StoredSource = ProjectSummary["sources"][number];
type PrometheusSource = Extract<StoredSource, { kind: "prometheus" }>;
type CloudflareSource = Extract<StoredSource, { kind: "cloudflare" }>;
type SentrySource = Extract<StoredSource, { kind: "sentry" }>;

export const prometheusSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is PrometheusSource => s.kind === "prometheus") ?? null;
export const cloudflareSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is CloudflareSource => s.kind === "cloudflare") ?? null;
export const sentrySource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is SentrySource => s.kind === "sentry") ?? null;

export function draftFrom(project: ProjectSummary): ProjectDraft {
  const source = prometheusSource(project);
  const cloudflare = cloudflareSource(project);
  const sentry = sentrySource(project);
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
    cloudflare: !!cloudflare,
    zoneId: cloudflare?.zone_id ?? "",
    hostnames: cloudflare?.hostnames.join(", ") ?? "",
    cfToken: "",
    cfApiUrl: cloudflare?.api_url ?? CLOUDFLARE_GRAPHQL_URL,
    sentry: !!sentry,
    sentryOrg: sentry?.organization ?? "",
    sentryProjects: sentry?.projects.join(", ") ?? "",
    sentryTags: sentry?.tags?.map((t) => ({ ...t })) ?? [],
    sentryEnvironment: sentry?.environment ?? "",
    sentryToken: "",
    sentryApiUrl: sentry?.api_url ?? SENTRY_URL,
    sentryTlsVerify: sentry?.tls_verify ?? true,
    scheduled: !!project.schedule,
    scheduleTime: project.schedule?.time ?? DEFAULT_SCHEDULE_TIME,
    scheduleTimezone: project.schedule?.timezone ?? browserTimezone(),
    scheduleWeekdays: [...(project.schedule?.weekdays ?? ALL_DAYS)],
    limitReports: project.keep_reports != null,
    keepReports: project.keep_reports ?? DEFAULT_KEEP_REPORTS,
  };
}

/** A new project prefilled from ``project``; stored secrets are reused server-side (clone_of). */
export function cloneDraft(project: ProjectSummary): ProjectDraft {
  return { ...draftFrom(project), name: `${project.name} (copy)`.slice(0, MAX_NAME_CHARS) };
}

export function storedAuth(project: ProjectSummary | null | undefined): StoredAuth | null {
  const auth = prometheusSource(project)?.auth;
  if (!auth) return null;
  if (auth.type === "bearer") return { type: "bearer", secretSet: auth.token_set };
  if (auth.type === "basic") return { type: "basic", secretSet: auth.password_set };
  return { type: "none", secretSet: false };
}

/** True when the project stores a Cloudflare API token an empty token input keeps. */
export const storedCloudflareToken = (project: ProjectSummary | null | undefined) => !!cloudflareSource(project)?.token_set;

/** True when the project stores a Sentry auth token an empty token input keeps. */
export const storedSentryToken = (project: ProjectSummary | null | undefined) => !!sentrySource(project)?.token_set;

/** True when the empty secret input for the chosen auth type keeps a stored secret. */
export const keepsStoredSecret = (draft: ProjectDraft, stored: StoredAuth | null) =>
  !!stored && stored.type === draft.authType && stored.secretSet;

/** Lower-cased, unique, sorted items of a comma- or space-separated list. */
export function parseList(raw: string): string[] {
  const items = raw
    .split(/[\s,]+/)
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return [...new Set(items)].sort();
}

export const parseHostnames = (raw: string) => parseList(raw.replace(/\.(?=[\s,]|$)/g, ""));

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

/** Labels scope Prometheus queries, so they are required only with a Prometheus source; without
 * one, rows left without a value are ignored (neither validated nor sent). */
export function validateMatchers(matchers: MatcherDraft[], required = true): FieldErrors {
  const errors: FieldErrors = {};
  const used = matchers.filter((m) => required || m.value);
  if (used.length === 0 && required) errors.matchers = "Add at least one label";
  if (used.length > MAX_MATCHERS) errors.matchers = `At most ${MAX_MATCHERS} labels`;

  const seen = new Set<string>();
  matchers.forEach((m, i) => {
    if (!required && !m.value) return;
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
    if (required) errors["prometheus.url"] = "Required to test the connection";
    return errors;
  }

  const bad = urlError(url);
  if (bad) errors["prometheus.url"] = bad;
  const keeps = keepsStoredSecret(draft, stored);
  if (draft.authType === "bearer" && !draft.token && !keeps) errors["prometheus.auth.token"] = "Required";
  if (draft.authType === "basic") {
    if (!draft.username.trim()) errors["prometheus.auth.username"] = "Required";
    if (!draft.password && !keeps) errors["prometheus.auth.password"] = "Required";
  }
  return errors;
}

export function validateCloudflare(draft: ProjectDraft, tokenStored: boolean): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.cloudflare) return errors;
  if (!ZONE_ID.test(draft.zoneId.trim().toLowerCase())) errors["cloudflare.zone_id"] = "Expected a zone ID of 32 hex characters";
  const hostnames = parseHostnames(draft.hostnames);
  const bad = hostnames.filter((h) => !HOSTNAME.test(h));
  if (bad.length) errors["cloudflare.hostnames"] = `Invalid hostnames: ${bad.join(", ")}`;
  else if (hostnames.length > MAX_HOSTNAMES) errors["cloudflare.hostnames"] = `At most ${MAX_HOSTNAMES} hostnames`;
  const apiUrl = draft.cfApiUrl.trim();
  const badUrl = apiUrl ? urlError(apiUrl) : "Required";
  if (badUrl) errors["cloudflare.api_url"] = badUrl;
  const synthetic = apiUrl.startsWith("synthetic:");
  if (!draft.cfToken && !tokenStored && !synthetic) errors["cloudflare.api_token"] = "Required";
  return errors;
}

export function validateSentry(draft: ProjectDraft, tokenStored: boolean): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.sentry) return errors;
  const slugError = "Lower-case letters, digits, - and _";
  if (!SENTRY_SLUG.test(draft.sentryOrg.trim().toLowerCase())) errors["sentry.organization"] = draft.sentryOrg.trim() ? slugError : "Required";
  const projects = parseList(draft.sentryProjects);
  const badProjects = projects.filter((p) => !SENTRY_SLUG.test(p));
  if (!projects.length) errors["sentry.projects"] = "Required";
  else if (badProjects.length) errors["sentry.projects"] = `Invalid project slugs: ${badProjects.join(", ")}`;
  else if (projects.length > MAX_SENTRY_PROJECTS) errors["sentry.projects"] = `At most ${MAX_SENTRY_PROJECTS} projects`;
  Object.assign(errors, validateSentryTags(draft.sentryTags));
  const environment = draft.sentryEnvironment.trim();
  if (environment && (!SENTRY_ENVIRONMENT.test(environment) || environment === "None")) errors["sentry.environment"] = "No spaces or /, at most 64 characters";
  const apiUrl = draft.sentryApiUrl.trim();
  const badUrl = apiUrl ? urlError(apiUrl) : "Required";
  if (badUrl) errors["sentry.api_url"] = badUrl;
  const synthetic = apiUrl.startsWith("synthetic:");
  if (!draft.sentryToken && !tokenStored && !synthetic) errors["sentry.auth_token"] = "Required";
  return errors;
}

export function validateSentryTags(tags: TagDraft[]): FieldErrors {
  const errors: FieldErrors = {};
  const seen = new Set<string>();
  if (usedTags(tags).length > MAX_SENTRY_TAGS) errors["sentry.tags"] = `At most ${MAX_SENTRY_TAGS} tags`;
  tags.forEach((t, i) => {
    const key = t.key.trim();
    if (!key && !t.value) return;
    if (!SENTRY_TAG_KEY.test(key)) errors[`sentry.tags.${i}.key`] = "Letters, digits, _ . : - (max 32)";
    else if (seen.has(key)) errors[`sentry.tags.${i}.key`] = "Duplicate tag";
    seen.add(key);
    if (!t.value) errors[`sentry.tags.${i}.value`] = "Required";
    else if (t.value.length > MAX_SENTRY_TAG_VALUE_CHARS) errors[`sentry.tags.${i}.value`] = `At most ${MAX_SENTRY_TAG_VALUE_CHARS} characters`;
    else if (/[\r\n]/.test(t.value)) errors[`sentry.tags.${i}.value`] = "No line breaks";
  });
  return errors;
}

const usedTags = (tags: TagDraft[]) => tags.filter((t) => t.key.trim() || t.value);

export function validateSchedule(draft: ProjectDraft): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.scheduled) return errors;
  if (!LOCAL_TIME.test(draft.scheduleTime)) errors["schedule.time"] = "Expected a time such as 08:00";
  if (!draft.scheduleTimezone.trim()) errors["schedule.timezone"] = "Required";
  if (draft.scheduleWeekdays.length === 0) errors["schedule.weekdays"] = "Choose at least one day";
  return errors;
}

export function validateRetention(draft: ProjectDraft): FieldErrors {
  if (!draft.limitReports) return {};
  const n = draft.keepReports;
  if (!Number.isInteger(n) || n < 1 || n > MAX_KEEP_REPORTS) return { keep_reports: `A whole number from 1 to ${MAX_KEEP_REPORTS}` };
  return {};
}

export function validateDraft(draft: ProjectDraft, stored: StoredAuth | null, cfTokenStored = false, sentryTokenStored = false): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = "Required";
  else if (name.length > MAX_NAME_CHARS) errors.name = `At most ${MAX_NAME_CHARS} characters`;
  return { ...errors, ...validateMatchers(draft.matchers, !!draft.url.trim()), ...validateSource(draft, stored), ...validateCloudflare(draft, cfTokenStored), ...validateSentry(draft, sentryTokenStored), ...validateSchedule(draft), ...validateRetention(draft) };
}

type SourceInput = NonNullable<ProjectInput["sources"]>[number];

export function prometheusInput(draft: ProjectDraft): SourceInput {
  const auth =
    draft.authType === "bearer"
      ? { type: "bearer" as const, ...(draft.token ? { token: draft.token } : {}) }
      : draft.authType === "basic"
        ? { type: "basic" as const, username: draft.username.trim(), ...(draft.password ? { password: draft.password } : {}) }
        : { type: "none" as const };
  return { kind: "prometheus", url: draft.url.trim(), tls_verify: draft.tlsVerify, auth };
}

export function cloudflareInput(draft: ProjectDraft): SourceInput {
  return {
    kind: "cloudflare",
    zone_id: draft.zoneId.trim().toLowerCase(),
    hostnames: parseHostnames(draft.hostnames),
    api_url: draft.cfApiUrl.trim(),
    ...(draft.cfToken ? { api_token: draft.cfToken } : {}),
  };
}

export function sentryInput(draft: ProjectDraft): SourceInput {
  return {
    kind: "sentry",
    organization: draft.sentryOrg.trim().toLowerCase(),
    projects: parseList(draft.sentryProjects),
    tags: usedTags(draft.sentryTags).map((t) => ({ key: t.key.trim(), value: t.value })),
    environment: draft.sentryEnvironment.trim() || null,
    api_url: draft.sentryApiUrl.trim(),
    tls_verify: draft.sentryTlsVerify,
    ...(draft.sentryToken ? { auth_token: draft.sentryToken } : {}),
  };
}

/** The input of one configured source kind. */
export function sourceInput(draft: ProjectDraft, kind: SourceKind): SourceInput {
  if (kind === "prometheus") return prometheusInput(draft);
  return kind === "cloudflare" ? cloudflareInput(draft) : sentryInput(draft);
}

/** Configured source kinds in the order they are sent (server errors refer to their index). */
export function sourceKinds(draft: ProjectDraft): SourceKind[] {
  const kinds: SourceKind[] = [];
  if (draft.url.trim()) kinds.push("prometheus");
  if (draft.cloudflare) kinds.push("cloudflare");
  if (draft.sentry) kinds.push("sentry");
  return kinds;
}

/** Matchers to send; without a Prometheus URL, rows left without a value are dropped. */
export const matchersInput = (draft: ProjectDraft) =>
  draft.matchers.filter((m) => draft.url.trim() || m.value).map((m) => ({ name: m.name.trim(), value: m.value }));

export function toInput(draft: ProjectDraft): ProjectInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    matchers: matchersInput(draft),
    sources: sourceKinds(draft).map((kind) => sourceInput(draft, kind)),
    schedule: draft.scheduled
      ? {
          time: draft.scheduleTime,
          timezone: draft.scheduleTimezone.trim(),
          weekdays: ALL_DAYS.filter((d) => draft.scheduleWeekdays.includes(d)),
        }
      : null,
    keep_reports: draft.limitReports ? draft.keepReports : null,
  };
}

/** "Daily at 08:00 (Europe/Berlin)", "Weekdays at …", "Mon, Wed at …". */
export function scheduleSummary(schedule: ReportSchedule): string {
  const days = schedule.weekdays ?? ALL_DAYS;
  const when =
    days.length === ALL_DAYS.length
      ? "Daily"
      : days.length === WORK_DAYS.length && WORK_DAYS.every((d) => days.includes(d))
        ? "Weekdays"
        : WEEKDAYS.filter((w) => days.includes(w.day))
            .map((w) => w.label)
            .join(", ");
  return `${when} at ${schedule.time} (${schedule.timezone})`;
}

/**
 * Field errors from a 422 problem, keyed like validateDraft ("matchers.0.name", "prometheus.url"):
 * "sources.<i>.x" refers to the i-th sent source, "source.x" to the tested one.
 */
export function serverFieldErrors(problem: Problem, kinds: SourceKind[] = [], tested?: SourceKind): FieldErrors {
  const errors: FieldErrors = {};
  for (const e of problem.errors ?? []) {
    let field = (e.field ?? "").replace(/^body\./, "");
    field = field.replace(/^sources\.(\d+)\./, (match, i: string) => (kinds[Number(i)] ? `${kinds[Number(i)]}.` : match));
    if (tested) field = field.replace(/^source\./, `${tested}.`);
    errors[field] ??= e.message ?? "Invalid";
  }
  return errors;
}

/** One line per source: "Prometheus · vm:8428/prom", "Cloudflare · shop.example.com", "Sentry · acme: shop-api, shop-web". */
export function sourceSummary(source: StoredSource): string {
  if (source.kind === "prometheus") return `Prometheus · ${sourceHost(source.url)}`;
  if (source.kind === "sentry") {
    const synthetic = source.api_url === SENTRY_URL ? "" : ` (${sourceHost(source.api_url)})`;
    const environment = source.environment ? ` · ${source.environment}` : "";
    return `Sentry · ${source.organization}: ${source.projects.join(", ")}${environment}${synthetic}`;
  }
  const synthetic = source.api_url.startsWith("synthetic:") ? ` (${sourceHost(source.api_url)})` : "";
  const scope = source.hostnames.length ? source.hostnames.join(", ") : `zone ${source.zone_id.slice(0, 8)}…`;
  return `Cloudflare · ${scope}${synthetic}`;
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

export type HealthKey = "ok" | "checking" | "unreachable" | "auth" | "no_series" | "no_traffic" | "not_configured" | "unreadable" | "error";

export const HEALTH_LABELS: Record<HealthKey, { label: string; icon: string }> = {
  ok: { label: "Reachable", icon: "✓" },
  checking: { label: "Checking…", icon: "◌" },
  unreachable: { label: "Unreachable", icon: "!" },
  auth: { label: "Auth failed", icon: "!" },
  no_series: { label: "No matching series", icon: "◐" },
  no_traffic: { label: "No traffic", icon: "◐" },
  not_configured: { label: "No source", icon: "–" },
  unreadable: { label: "Credentials unreadable", icon: "!" },
  error: { label: "Check failed", icon: "!" },
};

/** Most severe first: one failing source makes the whole project unhealthy. */
const HEALTH_RANK: HealthKey[] = ["unreachable", "auth", "no_series", "no_traffic", "ok"];

export function healthKey(
  project: ProjectSummary,
  health: ConnectionTest[] | undefined,
  state: { pending: boolean; failed: boolean },
): HealthKey {
  if (project.sources.length === 0) return "not_configured";
  if (!project.credentials_readable) return "unreadable";
  if (state.failed) return "error";
  if (state.pending || health === undefined) return "checking";
  if (health.length === 0) return "not_configured";
  return worstTest(health);
}

export function worstTest(tests: ConnectionTest[]): HealthKey {
  const keys = tests.map(testKey);
  return HEALTH_RANK.find((k) => keys.includes(k)) ?? "ok";
}

export function testKey(test: ConnectionTest): HealthKey {
  if (!test.reachable) return "unreachable";
  if (test.auth_ok === false) return "auth";
  // A quiet application legitimately reports no Sentry events, so only metrics and edge warn.
  if (test.matched_series === 0 && test.kind !== "sentry") return test.kind === "cloudflare" ? "no_traffic" : "no_series";
  return "ok";
}

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  prometheus: "Prometheus",
  cloudflare: "Cloudflare",
  sentry: "Sentry",
};

export const sourceKindLabel = (kind: SourceKind) => SOURCE_KIND_LABELS[kind];

/** What a connection test counts: matching series (Prometheus), requests (Cloudflare) or events (Sentry) in 24 h. */
export function testVolume(test: ConnectionTest): string | null {
  if (test.matched_series === null || test.matched_series === undefined) return null;
  if (test.kind === "cloudflare") return `${test.matched_series.toLocaleString("en")} requests in 24 h`;
  if (test.kind === "sentry") return `${test.matched_series.toLocaleString("en")} events in 24 h`;
  return `${test.matched_series} matching series`;
}

/** Every source of a report; 2.0 reports only have ``source``. */
export function reportSources(report: AnalysisReport): SourceInfo[] {
  if (report.sources?.length) return report.sources;
  return report.source ? [report.source] : [];
}
