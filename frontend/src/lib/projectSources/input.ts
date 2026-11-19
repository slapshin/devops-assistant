import type { ProjectInput } from "../../api/client";
import { sourceAuthInput } from "../auth";
import { ruleInput } from "../customRules";
import { type SourceDraft, type MatcherDraft, WAZUH_MONITORING_INDEX_PATTERN } from "./draft";
import { parseFolders, parseHostnames, parseList, parseNames } from "./lists";
import { usedMatchers, usedTags } from "./validation";

export type SourceInput = NonNullable<ProjectInput["sources"]>[number];

export type PrometheusSourceInput = Extract<SourceInput, { kind: "prometheus" }>;

export function prometheusInput(draft: SourceDraft): PrometheusSourceInput {
  return {
    kind: "prometheus",
    url: draft.url.trim(),
    tls_verify: draft.tlsVerify,
    auth: sourceAuthInput(draft.auth.prometheus),
    // Omitted is the same as none, so drafts without rules send what they always did.
    ...(draft.customRules.length ? { custom_rules: draft.customRules.map(ruleInput) } : {}),
  };
}

export function cloudflareInput(draft: SourceDraft): SourceInput {
  return {
    kind: "cloudflare",
    zone_id: draft.zoneId.trim().toLowerCase(),
    hostnames: parseHostnames(draft.hostnames),
    api_url: draft.cfApiUrl.trim(),
    auth: sourceAuthInput(draft.auth.cloudflare),
  } as SourceInput;
}

export function sentryInput(draft: SourceDraft): SourceInput {
  return {
    kind: "sentry",
    organization: draft.sentryOrg.trim().toLowerCase(),
    projects: parseList(draft.sentryProjects),
    tags: usedTags(draft.sentryTags).map((t) => ({ key: t.key.trim(), value: t.value })),
    environment: draft.sentryEnvironment.trim() || null,
    api_url: draft.sentryApiUrl.trim(),
    tls_verify: draft.sentryTlsVerify,
    auth: sourceAuthInput(draft.auth.sentry),
  } as SourceInput;
}

export function wazuhInput(draft: SourceDraft): SourceInput {
  return {
    kind: "wazuh",
    api_url: draft.wazuhUrl.trim(),
    index_pattern: draft.wazuhIndex.trim(),
    agents: parseNames(draft.wazuhAgents),
    groups: parseNames(draft.wazuhGroups),
    monitoring_index_pattern: draft.wazuhMonitoringIndex.trim() || WAZUH_MONITORING_INDEX_PATTERN,
    labels: usedTags(draft.wazuhLabels).map((l) => ({ key: l.key.trim(), value: l.value })),
    tls_verify: draft.wazuhTlsVerify,
    auth: sourceAuthInput(draft.auth.wazuh),
  } as SourceInput;
}

export function grafanaInput(draft: SourceDraft): SourceInput {
  const matchers = (rows: MatcherDraft[]) => usedMatchers(rows).map((m) => ({ name: m.name.trim(), op: m.op, value: m.value }));
  return {
    kind: "grafana",
    api_url: draft.grafanaUrl.trim(),
    org_id: draft.grafanaOrgId,
    folders: parseFolders(draft.grafanaFolders),
    rule_uids: parseNames(draft.grafanaRuleUids),
    rule_labels: matchers(draft.grafanaRuleLabels),
    instance_labels: matchers(draft.grafanaInstanceLabels),
    tls_verify: draft.grafanaTlsVerify,
    auth: sourceAuthInput(draft.auth.grafana),
  } as SourceInput;
}

/** Matchers to send; without a Prometheus URL, rows left without a value are dropped. */
export const matchersInput = (draft: SourceDraft) =>
  draft.matchers.filter((m) => draft.url.trim() || m.value).map((m) => ({ name: m.name.trim(), op: m.op, value: m.value }));
