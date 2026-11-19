import type { LabelMatcher, ProjectSummary, SourceKind } from "../../api/client";
import { credentialIdOf } from "../auth";
import { type CustomRuleDraft, ruleDraftFrom } from "../customRules";

/** Mirrors backend/app/domain/common.py and projects.py so most mistakes never reach the server. */
export const LABEL_NAME = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
export const RESERVED_LABEL_PREFIX = "__";
export const MAX_MATCHERS = 10;
export const MAX_LABEL_VALUE_CHARS = 256;
/** Mirrors backend/app/domain/projects.py (Cloudflare). */
export const CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql";
export const ZONE_ID = /^[0-9a-f]{32}$/;
export const HOSTNAME = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
export const MAX_HOSTNAMES = 20;
/** Mirrors backend/app/domain/projects.py (Sentry). */
export const SENTRY_URL = "https://sentry.io";
export const SENTRY_SLUG = /^[a-z0-9][a-z0-9_-]{0,99}$/;
export const SENTRY_ENVIRONMENT = /^[^\s/]{1,64}$/;
export const SENTRY_TAG_KEY = /^[a-zA-Z0-9_.:-]{1,32}$/;
export const MAX_SENTRY_PROJECTS = 10;
export const MAX_SENTRY_TAGS = 10;
export const MAX_SENTRY_TAG_VALUE_CHARS = 200;
/** Mirrors backend/app/domain/projects.py (Wazuh). */
export const WAZUH_INDEX_PATTERN = "wazuh-alerts-4.x-*";
export const WAZUH_AGENT = /^[A-Za-z0-9._-]{1,128}$/;
export const WAZUH_LABEL_KEY = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/;
export const WAZUH_INDEX = /^[a-z0-9_*][a-z0-9_.*+-]{0,254}$/;
export const MAX_WAZUH_AGENTS = 50;
export const MAX_WAZUH_LABELS = 10;
export const WAZUH_MONITORING_INDEX_PATTERN = "wazuh-monitoring-*";
export const WAZUH_GROUP = /^[A-Za-z0-9_.-]{1,255}$/;
export const MAX_WAZUH_GROUPS = 10;
export const MAX_WAZUH_LABEL_VALUE_CHARS = 200;
/** Mirrors backend/app/domain/projects.py (Grafana). */
export const GRAFANA_UID = /^[A-Za-z0-9_-]{1,40}$/;
export const MAX_GRAFANA_FOLDERS = 20;
export const MAX_GRAFANA_RULES = 100;
export const MAX_GRAFANA_FOLDER_CHARS = 255;

export type MatchOp = LabelMatcher["op"];

/** PromQL label matching operators; regular expressions are fully anchored (RE2 syntax). */
export const MATCH_OPS: { op: MatchOp; label: string }[] = [
  { op: "=", label: "equals" },
  { op: "!=", label: "does not equal" },
  { op: "=~", label: "matches regex" },
  { op: "!~", label: "does not match regex" },
];

export interface MatcherDraft {
  name: string;
  op: MatchOp;
  value: string;
}

export const emptyMatcher = (name = ""): MatcherDraft => ({ name, op: "=", value: "" });

export interface TagDraft {
  key: string;
  value: string;
}

export interface SourceDraft {
  matchers: MatcherDraft[];
  /** Prometheus source; an empty URL means none. */
  url: string;
  tlsVerify: boolean;
  /** The credential of each source kind, "" for none (kept while a source is switched off). */
  auth: Record<SourceKind, string>;
  /** Custom rules of the Prometheus source, in collection order. */
  customRules: CustomRuleDraft[];
  cloudflare: boolean;
  zoneId: string;
  /** Comma- or space-separated; empty analyses the whole zone. */
  hostnames: string;
  cfApiUrl: string;
  sentry: boolean;
  sentryOrg: string;
  /** Comma- or space-separated project slugs. */
  sentryProjects: string;
  /** Tag filters for every project; rows left completely empty are ignored. */
  sentryTags: TagDraft[];
  /** Empty analyses every environment. */
  sentryEnvironment: string;
  sentryApiUrl: string;
  sentryTlsVerify: boolean;
  wazuh: boolean;
  wazuhUrl: string;
  /** Comma- or space-separated agent names (case is kept). */
  wazuhAgents: string;
  /** Comma- or space-separated agent groups (case is kept); resolved via the monitoring index. */
  wazuhGroups: string;
  wazuhMonitoringIndex: string;
  /** Agent label filters; rows left completely empty are ignored. */
  wazuhLabels: TagDraft[];
  wazuhIndex: string;
  wazuhTlsVerify: boolean;
  grafana: boolean;
  grafanaUrl: string;
  grafanaOrgId: number;
  /** Comma- or newline-separated folder titles or UIDs (titles may contain spaces). */
  grafanaFolders: string;
  /** Comma- or space-separated alert rule UIDs. */
  grafanaRuleUids: string;
  /** Rule label matchers; rows left completely empty are ignored. */
  grafanaRuleLabels: MatcherDraft[];
  /** Alert instance label matchers; rows left completely empty are ignored. */
  grafanaInstanceLabels: MatcherDraft[];
  grafanaTlsVerify: boolean;
}

type StoredSource = ProjectSummary["sources"][number];
type PrometheusSource = Extract<StoredSource, { kind: "prometheus" }>;
type CloudflareSource = Extract<StoredSource, { kind: "cloudflare" }>;
type SentrySource = Extract<StoredSource, { kind: "sentry" }>;
type WazuhSource = Extract<StoredSource, { kind: "wazuh" }>;
type GrafanaSource = Extract<StoredSource, { kind: "grafana" }>;

export const prometheusSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is PrometheusSource => s.kind === "prometheus") ?? null;
export const cloudflareSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is CloudflareSource => s.kind === "cloudflare") ?? null;
export const sentrySource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is SentrySource => s.kind === "sentry") ?? null;
export const wazuhSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is WazuhSource => s.kind === "wazuh") ?? null;
export const grafanaSource = (project: { sources: StoredSource[] } | null | undefined) =>
  project?.sources.find((s): s is GrafanaSource => s.kind === "grafana") ?? null;

export function emptySourceDraft(): SourceDraft {
  return {
    matchers: [
      emptyMatcher("project"),
      emptyMatcher("env"),
    ],
    url: "",
    tlsVerify: true,
    auth: { prometheus: "", cloudflare: "", sentry: "", wazuh: "", grafana: "" },
    customRules: [],
    cloudflare: false,
    zoneId: "",
    hostnames: "",
    cfApiUrl: CLOUDFLARE_GRAPHQL_URL,
    sentry: false,
    sentryOrg: "",
    sentryProjects: "",
    sentryTags: [],
    sentryEnvironment: "",
    sentryApiUrl: SENTRY_URL,
    sentryTlsVerify: true,
    wazuh: false,
    wazuhUrl: "",
    wazuhAgents: "",
    wazuhGroups: "",
    wazuhMonitoringIndex: WAZUH_MONITORING_INDEX_PATTERN,
    wazuhLabels: [],
    wazuhIndex: WAZUH_INDEX_PATTERN,
    wazuhTlsVerify: true,
    grafana: false,
    grafanaUrl: "",
    grafanaOrgId: 1,
    grafanaFolders: "",
    grafanaRuleUids: "",
    grafanaRuleLabels: [],
    grafanaInstanceLabels: [],
    grafanaTlsVerify: true,
  };
}

export function sourceDraftFrom(project: ProjectSummary): SourceDraft {
  const source = prometheusSource(project);
  const cloudflare = cloudflareSource(project);
  const sentry = sentrySource(project);
  const wazuh = wazuhSource(project);
  const grafana = grafanaSource(project);
  return {
    matchers: project.matchers.map((m) => ({ ...m })),
    url: source?.url ?? "",
    tlsVerify: source?.tls_verify ?? true,
    auth: {
      prometheus: credentialIdOf(source?.auth),
      cloudflare: credentialIdOf(cloudflare?.auth),
      sentry: credentialIdOf(sentry?.auth),
      wazuh: credentialIdOf(wazuh?.auth),
      grafana: credentialIdOf(grafana?.auth),
    },
    customRules: (source?.custom_rules ?? []).map(ruleDraftFrom),
    cloudflare: !!cloudflare,
    zoneId: cloudflare?.zone_id ?? "",
    hostnames: cloudflare?.hostnames.join(", ") ?? "",
    cfApiUrl: cloudflare?.api_url ?? CLOUDFLARE_GRAPHQL_URL,
    sentry: !!sentry,
    sentryOrg: sentry?.organization ?? "",
    sentryProjects: sentry?.projects.join(", ") ?? "",
    sentryTags: sentry?.tags?.map((t) => ({ ...t })) ?? [],
    sentryEnvironment: sentry?.environment ?? "",
    sentryApiUrl: sentry?.api_url ?? SENTRY_URL,
    sentryTlsVerify: sentry?.tls_verify ?? true,
    wazuh: !!wazuh,
    wazuhUrl: wazuh?.api_url ?? "",
    wazuhAgents: wazuh?.agents?.join(", ") ?? "",
    wazuhGroups: wazuh?.groups?.join(", ") ?? "",
    wazuhMonitoringIndex: wazuh?.monitoring_index_pattern ?? WAZUH_MONITORING_INDEX_PATTERN,
    wazuhLabels: wazuh?.labels?.map((l) => ({ ...l })) ?? [],
    wazuhIndex: wazuh?.index_pattern ?? WAZUH_INDEX_PATTERN,
    wazuhTlsVerify: wazuh?.tls_verify ?? true,
    grafana: !!grafana,
    grafanaUrl: grafana?.api_url ?? "",
    grafanaOrgId: grafana?.org_id ?? 1,
    grafanaFolders: grafana?.folders?.join(", ") ?? "",
    grafanaRuleUids: grafana?.rule_uids?.join(", ") ?? "",
    grafanaRuleLabels: grafana?.rule_labels?.map((m) => ({ ...m })) ?? [],
    grafanaInstanceLabels: grafana?.instance_labels?.map((m) => ({ ...m })) ?? [],
    grafanaTlsVerify: grafana?.tls_verify ?? true,
  };
}
