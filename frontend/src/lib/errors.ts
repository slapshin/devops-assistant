import type { ErrorKindActivity, Finding, SourceInfo } from "../api/client";

const ROW_TITLE_CHARS = 60;

/** The error kind with the most events behind a finding, for a table cell: "KeyError: 'currency' +3". */
export function errorKindsText(finding: Finding): string {
  const kinds = finding.error_kinds ?? [];
  const top = kinds[0];
  if (!top) return "";

  const title = top.title ?? top.issue;
  const short = title.length > ROW_TITLE_CHARS ? `${title.slice(0, ROW_TITLE_CHARS - 1)}…` : title;
  return short + (kinds.length > 1 ? ` +${kinds.length - 1}` : "");
}

/** The issue's page in Sentry, found by its short ID; none for synthetic sources or "(other errors)". */
export function issueLink(kind: ErrorKindActivity, finding: Finding, sources: SourceInfo[]): string | null {
  const base = sources.find((s) => s.kind === "sentry")?.base_url ?? "";
  const organization = finding.entity.labels.organization;
  if (!kind.title || !organization || !/^https?:\/\//.test(base)) return null;

  return `${base.replace(/\/+$/, "")}/organizations/${encodeURIComponent(organization)}/issues/?query=${encodeURIComponent(kind.issue)}`;
}
