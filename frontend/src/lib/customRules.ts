import type { AnalysisReport, CustomRule, Finding, Schemas } from "../api/client";
import type { FieldErrors } from "./projects";
import { formatValue } from "./format";

/** Mirrors backend/app/domain/custom_rules.py so most mistakes never reach the server. */
export const MAX_CUSTOM_RULES = 20;
const SLUG = /^[a-z][a-z0-9_]{0,39}$/;
const LABEL_NAME = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const MAX_TITLE_CHARS = 80;
const MAX_QUERY_CHARS = 2000;
const MAX_IDENTITY_LABELS = 10;
const MIN_DURATION_MINUTES = 5;
const MAX_DURATION_MINUTES = 1440;
const DEFAULT_DURATION_MINUTES = 15;
const SLUG_MAX_CHARS = 40;
export const CUSTOM_SIGNAL_PREFIX = "custom_";

export type RuleMode = Schemas["CustomRuleMode"];
export type RuleDirection = Schemas["ChangeDirection"];
export type ThresholdOp = Schemas["ThresholdOp"];
export type Unit = Schemas["Unit"];
export type Severity = Schemas["Severity"];

export const RULE_MODES: { mode: RuleMode; label: string; hint: string }[] = [
  { mode: "anomaly", label: "Anomaly", hint: "Unusual compared with the same series on preceding days." },
  { mode: "threshold", label: "Threshold", hint: "At or beyond a fixed value for a minimum time, like an alert rule." },
  { mode: "both", label: "Anomaly or threshold", hint: "Either of the two." },
  { mode: "event", label: "Event", hint: "Any value of 1 or more in a 5-min step, e.g. failed jobs." },
];

export const RULE_DIRECTIONS: { direction: RuleDirection; label: string }[] = [
  { direction: "up", label: "Increases" },
  { direction: "down", label: "Decreases" },
  { direction: "both", label: "Both" },
];

export const RULE_UNITS: { unit: Unit; label: string }[] = [
  { unit: "count", label: "Count" },
  { unit: "per_second", label: "Per second" },
  { unit: "requests_per_second", label: "Requests per second" },
  { unit: "ratio", label: "Ratio (0–1, shown as %)" },
  { unit: "seconds", label: "Seconds" },
  { unit: "bytes", label: "Bytes" },
  { unit: "bytes_per_second", label: "Bytes per second" },
];

export const RULE_SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];

export interface CustomRuleDraft {
  slug: string;
  title: string;
  description: string;
  query: string;
  unit: Unit;
  mode: RuleMode;
  direction: RuleDirection;
  /** Numbers are edited as text so an empty field means "not set". */
  threshold: string;
  criticalThreshold: string;
  thresholdOp: ThresholdOp;
  minDurationMinutes: number;
  minChangeFactor: string;
  minChange: string;
  maxSeverity: Severity | "";
  /** Comma- or space-separated label names. */
  identityLabels: string;
}

export const emptyRule = (): CustomRuleDraft => ({
  slug: "",
  title: "",
  description: "",
  query: "",
  unit: "count",
  mode: "anomaly",
  direction: "up",
  threshold: "",
  criticalThreshold: "",
  thresholdOp: "above",
  minDurationMinutes: DEFAULT_DURATION_MINUTES,
  minChangeFactor: "",
  minChange: "",
  maxSeverity: "",
  identityLabels: "",
});

const numberText = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));

export function ruleDraftFrom(rule: CustomRule): CustomRuleDraft {
  return {
    slug: rule.slug,
    title: rule.title,
    description: rule.description,
    query: rule.query,
    unit: rule.unit,
    mode: rule.mode,
    direction: rule.direction,
    threshold: numberText(rule.threshold),
    criticalThreshold: numberText(rule.critical_threshold),
    thresholdOp: rule.threshold_op,
    minDurationMinutes: rule.min_duration_minutes,
    minChangeFactor: numberText(rule.min_change_factor),
    minChange: numberText(rule.min_change),
    maxSeverity: rule.max_severity ?? "",
    identityLabels: (rule.identity_labels ?? []).join(", "),
  };
}

const usesThreshold = (mode: RuleMode) => mode === "threshold" || mode === "both";
export const usesBaseline = (mode: RuleMode) => mode === "anomaly" || mode === "both";
export const showsThreshold = usesThreshold;

const parseNumber = (text: string) => (text.trim() === "" ? null : Number(text.trim()));
const parseLabels = (text: string) => text.split(/[\s,]+/).filter(Boolean);

/** "Checkout failures!" → "checkout_failures". */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^[^a-z]+/, "")
    .slice(0, SLUG_MAX_CHARS)
    .replace(/_+$/, "");
}

export function ruleInput(draft: CustomRuleDraft): CustomRule {
  const threshold = usesThreshold(draft.mode);
  const baseline = usesBaseline(draft.mode);
  return {
    slug: draft.slug.trim(),
    title: draft.title.trim(),
    description: draft.description.trim(),
    query: draft.query.trim(),
    unit: draft.unit,
    mode: draft.mode,
    direction: draft.direction,
    threshold: threshold ? parseNumber(draft.threshold) : null,
    critical_threshold: threshold ? parseNumber(draft.criticalThreshold) : null,
    threshold_op: draft.thresholdOp,
    min_duration_minutes: draft.minDurationMinutes,
    min_change_factor: baseline ? parseNumber(draft.minChangeFactor) : null,
    min_change: baseline ? parseNumber(draft.minChange) : null,
    max_severity: draft.maxSeverity || null,
    identity_labels: parseLabels(draft.identityLabels),
  };
}

function numberError(text: string, { required = false, above }: { required?: boolean; above?: number } = {}): string | null {
  const value = parseNumber(text);
  if (value === null) return required ? "Required" : null;
  if (!Number.isFinite(value)) return "A number";
  if (above !== undefined && value <= above) return `Greater than ${above}`;
  return null;
}

/** Errors keyed like the server's ("prometheus.custom_rules.<i>.<field>"). */
export function validateCustomRules(rules: CustomRuleDraft[]): FieldErrors {
  const errors: FieldErrors = {};
  const seen = new Set<string>();
  rules.forEach((r, i) => {
    const at = (field: string) => `prometheus.custom_rules.${i}.${field}`;
    const slug = r.slug.trim();
    if (!slug) errors[at("slug")] = "Required";
    else if (!SLUG.test(slug)) errors[at("slug")] = "Lower-case letters, digits and _; starts with a letter; at most 40 characters";
    else if (seen.has(slug)) errors[at("slug")] = "Another rule uses this identifier";
    seen.add(slug);

    if (!r.title.trim()) errors[at("title")] = "Required";
    else if (r.title.trim().length > MAX_TITLE_CHARS) errors[at("title")] = `At most ${MAX_TITLE_CHARS} characters`;

    const query = r.query.trim();
    if (!query) errors[at("query")] = "Required";
    else if (query.length > MAX_QUERY_CHARS) errors[at("query")] = `At most ${MAX_QUERY_CHARS} characters`;

    if (usesThreshold(r.mode)) {
      const thresholdError = numberError(r.threshold, { required: true });
      if (thresholdError) errors[at("threshold")] = thresholdError;
      const criticalError = numberError(r.criticalThreshold);
      if (criticalError) errors[at("critical_threshold")] = criticalError;
      const [t, c] = [parseNumber(r.threshold), parseNumber(r.criticalThreshold)];
      if (!thresholdError && !criticalError && t !== null && c !== null) {
        const beyond = r.thresholdOp === "above" ? c > t : c < t;
        if (!beyond) errors[at("critical_threshold")] = `Must be ${r.thresholdOp} the threshold`;
      }
      const minutes = r.minDurationMinutes;
      if (!Number.isInteger(minutes) || minutes < MIN_DURATION_MINUTES || minutes > MAX_DURATION_MINUTES) {
        errors[at("min_duration_minutes")] = `A whole number from ${MIN_DURATION_MINUTES} to ${MAX_DURATION_MINUTES}`;
      }
    }
    if (usesBaseline(r.mode)) {
      const factorError = numberError(r.minChangeFactor, { above: 1 });
      if (factorError) errors[at("min_change_factor")] = factorError;
      const changeError = numberError(r.minChange, { above: 0 });
      if (changeError) errors[at("min_change")] = changeError;
    }

    const labels = parseLabels(r.identityLabels);
    const bad = labels.filter((l) => !LABEL_NAME.test(l) || l.startsWith("__"));
    if (bad.length) errors[at("identity_labels")] = `Invalid label names: ${bad.join(", ")}`;
    else if (new Set(labels).size !== labels.length) errors[at("identity_labels")] = "Duplicate label names";
    else if (labels.length > MAX_IDENTITY_LABELS) errors[at("identity_labels")] = `At most ${MAX_IDENTITY_LABELS} labels`;
  });
  if (rules.length > MAX_CUSTOM_RULES) errors["prometheus.custom_rules"] = `At most ${MAX_CUSTOM_RULES} rules`;
  return errors;
}

/** Whether any error belongs to rule ``index`` (including rule-level errors without a field). */
export const ruleHasErrors = (errors: FieldErrors, index: number) =>
  Object.keys(errors).some((k) => k === `prometheus.custom_rules.${index}` || k.startsWith(`prometheus.custom_rules.${index}.`));

/** One line on how a rule decides: "Anomaly (increases)", "≥ 500 for 10 min", … */
export function ruleSummary(rule: CustomRule): string {
  const op = rule.threshold_op === "above" ? "≥" : "≤";
  const threshold =
    rule.threshold === null || rule.threshold === undefined
      ? ""
      : `${op} ${formatValue(rule.threshold, rule.unit)} for ${rule.min_duration_minutes} min`;
  const direction = RULE_DIRECTIONS.find((d) => d.direction === rule.direction)?.label.toLowerCase() ?? rule.direction;
  switch (rule.mode) {
    case "anomaly":
      return `Anomaly (${direction})`;
    case "threshold":
      return threshold;
    case "both":
      return `Anomaly (${direction}) or ${threshold}`;
    case "event":
      return "Event (any value ≥ 1 per step)";
  }
}

/** The custom rule a finding came from, as it was when the report was made. */
export function findingRule(report: AnalysisReport, finding: Finding): CustomRule | null {
  if (finding.family !== "custom") return null;
  return (report.custom_rules ?? []).find((r) => `${CUSTOM_SIGNAL_PREFIX}${r.slug}` === finding.signal) ?? null;
}
