import { type FieldErrors, validateSourceCredential } from "../auth";
import {
  LABEL_NAME,
  RESERVED_LABEL_PREFIX,
  MAX_MATCHERS,
  MAX_LABEL_VALUE_CHARS,
  ZONE_ID,
  HOSTNAME,
  MAX_HOSTNAMES,
  SENTRY_SLUG,
  SENTRY_ENVIRONMENT,
  SENTRY_TAG_KEY,
  MAX_SENTRY_PROJECTS,
  MAX_SENTRY_TAGS,
  MAX_SENTRY_TAG_VALUE_CHARS,
  WAZUH_AGENT,
  WAZUH_LABEL_KEY,
  WAZUH_INDEX,
  MAX_WAZUH_AGENTS,
  MAX_WAZUH_LABELS,
  WAZUH_GROUP,
  MAX_WAZUH_GROUPS,
  MAX_WAZUH_LABEL_VALUE_CHARS,
  GRAFANA_UID,
  MAX_GRAFANA_FOLDERS,
  MAX_GRAFANA_RULES,
  MAX_GRAFANA_FOLDER_CHARS,
  type MatcherDraft,
  type TagDraft,
  type SourceDraft,
} from "./draft";
import { parseFolders, parseHostnames, parseList, parseNames } from "./lists";

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
  // A negative matcher alone would select nearly every series (the server also rejects a
  // regex that matches an empty value, which RE2 syntax keeps us from checking here).
  else if (required && !used.some((m) => m.op === "=" || m.op === "=~")) errors.matchers = "Add at least one = or =~ label";
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

export function validateSource(draft: SourceDraft, required = false): FieldErrors {
  const errors: FieldErrors = {};
  const url = draft.url.trim();
  if (!url) {
    if (required) errors["prometheus.url"] = "Required to test the connection";
    return errors;
  }

  const bad = urlError(url);
  if (bad) errors["prometheus.url"] = bad;
  return errors;
}

export function validateCloudflare(draft: SourceDraft): FieldErrors {
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
  return { ...errors, ...validateSourceCredential("cloudflare", draft.auth.cloudflare, synthetic) };
}

export function validateSentry(draft: SourceDraft): FieldErrors {
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
  return { ...errors, ...validateSourceCredential("sentry", draft.auth.sentry, synthetic) };
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

export const usedTags = (tags: TagDraft[]) => tags.filter((t) => t.key.trim() || t.value);

export function validateWazuh(draft: SourceDraft): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.wazuh) return errors;
  const apiUrl = draft.wazuhUrl.trim();
  const badUrl = apiUrl ? urlError(apiUrl) : "Required";
  if (badUrl) errors["wazuh.api_url"] = badUrl;
  const agents = parseNames(draft.wazuhAgents);
  const badAgents = agents.filter((a) => !WAZUH_AGENT.test(a));
  if (badAgents.length) errors["wazuh.agents"] = `Invalid agent names: ${badAgents.join(", ")}`;
  else if (agents.length > MAX_WAZUH_AGENTS) errors["wazuh.agents"] = `At most ${MAX_WAZUH_AGENTS} agents`;
  const groups = parseNames(draft.wazuhGroups);
  const badGroups = groups.filter((g) => !WAZUH_GROUP.test(g) || g === "." || g === "..");
  if (badGroups.length) errors["wazuh.groups"] = `Invalid group names: ${badGroups.join(", ")}`;
  else if (groups.length > MAX_WAZUH_GROUPS) errors["wazuh.groups"] = `At most ${MAX_WAZUH_GROUPS} groups`;
  if (groups.length && !WAZUH_INDEX.test(draft.wazuhMonitoringIndex.trim())) errors["wazuh.monitoring_index_pattern"] = "One lower-case index pattern, e.g. wazuh-monitoring-*";
  const labels = usedTags(draft.wazuhLabels);
  if (labels.length > MAX_WAZUH_LABELS) errors["wazuh.labels"] = `At most ${MAX_WAZUH_LABELS} labels`;
  const seen = new Set<string>();
  draft.wazuhLabels.forEach((l, i) => {
    const key = l.key.trim();
    if (!key && !l.value) return;
    if (!WAZUH_LABEL_KEY.test(key)) errors[`wazuh.labels.${i}.key`] = "Letters, digits, _ . - (max 64)";
    else if (seen.has(key)) errors[`wazuh.labels.${i}.key`] = "Duplicate label";
    seen.add(key);
    if (!l.value) errors[`wazuh.labels.${i}.value`] = "Required";
    else if (l.value.length > MAX_WAZUH_LABEL_VALUE_CHARS) errors[`wazuh.labels.${i}.value`] = `At most ${MAX_WAZUH_LABEL_VALUE_CHARS} characters`;
    else if (/[\r\n]/.test(l.value)) errors[`wazuh.labels.${i}.value`] = "No line breaks";
  });
  if (!agents.length && !groups.length && !labels.length) errors["wazuh.agents"] ??= "Select agents by name, group or label";
  if (!WAZUH_INDEX.test(draft.wazuhIndex.trim())) errors["wazuh.index_pattern"] = "One lower-case index pattern, e.g. wazuh-alerts-4.x-*";
  const synthetic = apiUrl.startsWith("synthetic:");
  return { ...errors, ...validateSourceCredential("wazuh", draft.auth.wazuh, synthetic) };
}

export const usedMatchers = (rows: MatcherDraft[]) => rows.filter((m) => m.name.trim() || m.value);

/** Optional label matcher rows of a source, e.g. "grafana.instance_labels". */
function validateMatcherRows(rows: MatcherDraft[], field: string): FieldErrors {
  const errors: FieldErrors = {};
  if (usedMatchers(rows).length > MAX_MATCHERS) errors[field] = `At most ${MAX_MATCHERS} labels`;
  const seen = new Set<string>();
  rows.forEach((m, i) => {
    const name = m.name.trim();
    if (!name && !m.value) return;
    if (!LABEL_NAME.test(name)) errors[`${field}.${i}.name`] = "Letters, digits and _; must not start with a digit";
    else if (name.startsWith(RESERVED_LABEL_PREFIX)) errors[`${field}.${i}.name`] = "Labels starting with __ are reserved";
    else if (seen.has(name)) errors[`${field}.${i}.name`] = "Duplicate label";
    seen.add(name);
    if (!m.value) errors[`${field}.${i}.value`] = "Required";
    else if (m.value.length > MAX_LABEL_VALUE_CHARS) errors[`${field}.${i}.value`] = `At most ${MAX_LABEL_VALUE_CHARS} characters`;
  });
  return errors;
}

export function validateGrafana(draft: SourceDraft): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.grafana) return errors;
  const apiUrl = draft.grafanaUrl.trim();
  const badUrl = apiUrl ? urlError(apiUrl) : "Required";
  if (badUrl) errors["grafana.api_url"] = badUrl;
  if (!Number.isInteger(draft.grafanaOrgId) || draft.grafanaOrgId < 1) errors["grafana.org_id"] = "A whole number of at least 1";
  const folders = parseFolders(draft.grafanaFolders);
  if (folders.some((f) => f.length > MAX_GRAFANA_FOLDER_CHARS)) errors["grafana.folders"] = `At most ${MAX_GRAFANA_FOLDER_CHARS} characters per folder`;
  else if (folders.length > MAX_GRAFANA_FOLDERS) errors["grafana.folders"] = `At most ${MAX_GRAFANA_FOLDERS} folders`;
  const uids = parseNames(draft.grafanaRuleUids);
  const badUids = uids.filter((u) => !GRAFANA_UID.test(u));
  if (badUids.length) errors["grafana.rule_uids"] = `Invalid rule UIDs: ${badUids.join(", ")}`;
  else if (uids.length > MAX_GRAFANA_RULES) errors["grafana.rule_uids"] = `At most ${MAX_GRAFANA_RULES} rules`;
  Object.assign(errors, validateMatcherRows(draft.grafanaRuleLabels, "grafana.rule_labels"));
  Object.assign(errors, validateMatcherRows(draft.grafanaInstanceLabels, "grafana.instance_labels"));
  if (!folders.length && !uids.length && !usedMatchers(draft.grafanaRuleLabels).length) {
    errors["grafana.folders"] ??= "Select alert rules by folder, UID or rule label";
  }
  const synthetic = apiUrl.startsWith("synthetic:");
  return { ...errors, ...validateSourceCredential("grafana", draft.auth.grafana, synthetic) };
}
