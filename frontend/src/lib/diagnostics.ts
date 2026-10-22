import type { FilterCheck, ReportSummary, ScopeDiagnostics, SourceKind } from "../api/client";
import { SOURCE_KIND_LABELS, reportSources } from "./projects";

/** Mirrors the `source_unavailable` exclusion message of backend/app/service.py. */
const failurePrefix = (kind: SourceKind) => `${kind} source failed`;

/** What a filter check says about its part (docs/UI_SPEC.md, Scope tab). */
export type CheckVerdict = "excludes_all" | "no_such_value" | "matches_nothing" | "no_effect" | "ok" | "unknown";

export const VERDICT_LABELS: Record<CheckVerdict, string> = {
  excludes_all: "Excludes all data",
  no_such_value: "No such value",
  matches_nothing: "Matches nothing",
  no_effect: "No effect",
  ok: "OK",
  unknown: "Unknown",
};

/** Verdicts that point at a wrong filter. */
export const PROBLEM_VERDICTS = new Set<CheckVerdict>(["excludes_all", "no_such_value", "matches_nothing"]);

export const FIELD_LABELS: Record<FilterCheck["field"], string> = {
  matcher: "Label matcher",
  zone: "Zone",
  hostname: "Hostname",
  project: "Project",
  environment: "Environment",
  tag: "Tag",
  agent: "Agent",
  group: "Group",
  label: "Agent label",
  folder: "Folder",
  rule_uid: "Rule UID",
  rule_label: "Rule label",
  instance_label: "Instance label",
};

export function checkVerdict(check: FilterCheck): CheckVerdict {
  const without = check.matched_without ?? null;
  const values = check.available ?? [];
  if (check.matched === null) return "unknown";
  if (check.matched === 0 && without !== null && without > 0) return "excludes_all";
  if (values.length && !(check.matching ?? []).length && !check.available_truncated) return "no_such_value";
  if (check.matched === 0) return "matches_nothing";
  if (without !== null && check.matched === without) return "no_effect";
  return "ok";
}

/** `{a="1", b="2"}`, as in PromQL. */
export const labelText = (labels: Record<string, string>) =>
  `{${Object.entries(labels).map(([k, v]) => `${k}="${v}"`).join(", ")}}`;

/** Units come plural from the server ("agents", "series"); one of them reads singular. */
export const countText = (n: number | null | undefined, unit: string) =>
  n === null || n === undefined ? "—" : `${n.toLocaleString("en")} ${n === 1 && unit !== "series" ? unit.replace(/s$/, "") : unit}`;

/** Each analysed source in report order with its snapshot (null: the source failed or the report predates 2.8). */
export function sourceDiagnostics(report: ReportSummary): { kind: SourceKind; baseUrl: string; diagnostics: Required<ScopeDiagnostics> | null; failures: string[] }[] {
  const byKind = new Map((report.diagnostics ?? []).map((d) => [d.kind, withDefaults(d)]));
  const failed = report.exclusions.filter((e) => e.code === "source_unavailable");
  const seen = new Set<SourceKind>();
  const rows = [];
  for (const source of reportSources(report)) {
    const kind = source.kind ?? "prometheus";
    seen.add(kind);
    rows.push({ kind, baseUrl: source.base_url, diagnostics: byKind.get(kind) ?? null, failures: [] as string[] });
  }
  for (const kind of Object.keys(SOURCE_KIND_LABELS) as SourceKind[]) {
    if (!seen.has(kind) && failed.some((e) => e.message.startsWith(failurePrefix(kind)))) {
      rows.push({ kind, baseUrl: "", diagnostics: null, failures: [] });
    }
  }
  for (const row of rows) {
    row.failures = [...new Set(failed.filter((e) => e.message.startsWith(failurePrefix(row.kind))).map((e) => e.message))];
  }
  return rows;
}

const withDefaults = (d: ScopeDiagnostics): Required<ScopeDiagnostics> => ({
  ...d,
  selection: d.selection ?? [],
  samples: d.samples ?? [],
  checks: d.checks ?? [],
  requests: d.requests ?? [],
  notes: d.notes ?? [],
});

/** Filter checks pointing at a wrong filter, over every source. */
export const problemCount = (report: ReportSummary) =>
  (report.diagnostics ?? []).flatMap((d) => d.checks ?? []).filter((c) => PROBLEM_VERDICTS.has(checkVerdict(c))).length;
