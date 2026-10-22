import type { AnalysisReport, FindingDetail, ReportSummary } from "./api/client";

/** Test helpers: the API's report summary and finding detail of a full report fixture, sliced as
 * the server does (`AnalysisReport.summary()` and `.finding_detail()`). */

export function reportSummary(report: AnalysisReport): ReportSummary {
  return Object.fromEntries(Object.entries(report).filter(([key]) => key !== "evidence" && key !== "related_metrics")) as ReportSummary;
}

export function findingDetail(report: AnalysisReport, findingId: string): FindingDetail {
  const finding = report.findings.find((f) => f.finding_id === findingId);
  if (!finding) throw new Error(`no finding ${findingId} in report ${report.analysis_id}`);

  const evidence = new Map(report.evidence.map((e) => [e.evidence_id, e]));
  const related = report.related_metrics;
  if (!related) {
    return { analysis_id: report.analysis_id, finding_id: findingId, evidence: finding.evidence_ids.map((id) => evidence.get(id)!), related: null };
  }

  const comparisons = related.comparisons.filter((c) => c.finding_id === findingId).sort((a, b) => a.rank - b.rank);
  const chartIds = new Set(comparisons.flatMap((c) => [c.anchor.chart_id, c.candidate.chart_id]));
  return {
    analysis_id: report.analysis_id,
    finding_id: findingId,
    evidence: finding.evidence_ids.map((id) => evidence.get(id)!),
    related: {
      policy: related.policy,
      evaluation: related.evaluations.find((e) => e.finding_id === findingId) ?? null,
      comparisons,
      charts: related.charts.filter((c) => chartIds.has(c.chart_id)),
    },
  };
}
