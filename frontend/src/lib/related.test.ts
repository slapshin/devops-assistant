import { describe, expect, it } from "vitest";

import related from "../../../fixtures/reports/report_related.json";
import anomalies from "../../../fixtures/reports/report_anomalies.json";
import type { AnalysisReport, RelatedMetric } from "../api/client";
import { evaluationCounts, evaluationMessage, pairRows, relatedFor, relatedPairOption, similarityText, timingText } from "./related";
import { timestamps } from "./charts";
import type { ChartPalette } from "./theme";
import { findingDetail } from "../test-report";

const report = related as unknown as AnalysisReport;
const errors = report.findings.find((f) => f.signal === "server_error_ratio")!;

const palette: ChartPalette = {
  font: "sans-serif", bg: "#fff", text: "#000", muted: "#666", border: "#ccc", grid: "#eee",
  observed: "#00f", expected: "#00f", band: "#ddf", severity: {},
};

const comparison = (changes: Partial<RelatedMetric>) => ({ ...relatedFor(findingDetail(report, errors.finding_id)).comparisons[0], ...changes }) as RelatedMetric;

describe("relatedFor", () => {
  it("lists a finding's comparisons best first with its evaluation", () => {
    const found = relatedFor(findingDetail(report, errors.finding_id));

    expect(found.comparisons.map((c) => [c.rank, c.candidate.signal])).toEqual([
      [1, "latency_p95"],
      [2, "database_deadlocks"],
    ]);
    expect(found.evaluation?.state).toBe("evaluated");
    expect(evaluationMessage(found)).toBeNull();
  });

  it("explains reports that predate related metrics", () => {
    const old = anomalies as unknown as AnalysisReport;

    const found = relatedFor(findingDetail(old, old.findings[0]!.finding_id));

    expect(found.related).toBeNull();
    expect(evaluationMessage(found)).toMatch(/predates related metrics/);
  });

  it("never reads an empty result as healthy and states what was considered", () => {
    const found = relatedFor(findingDetail(report, errors.finding_id));
    const evaluation = { ...found.evaluation!, retained: 0, candidates: 4, evaluated: 4, omitted: 0, shared_input: 1, unavailable: [{ code: "insufficient_pairs", count: 2 }] };

    const message = evaluationMessage({ ...found, comparisons: [], evaluation });

    expect(message).toBe("No other collected metric changed in a similar way around this finding.");
    expect(evaluationCounts(evaluation)).toBe(
      "4 with an unusual change in the window · 4 compared · 1 computed from the same data, skipped · 2 unavailable (insufficient pairs)",
    );
  });

  it("states budget skips and drops", () => {
    const found = relatedFor(findingDetail(report, errors.finding_id));

    expect(evaluationMessage({ ...found, evaluation: { ...found.evaluation!, state: "skipped_budget" } })).toMatch(/budget/);
    expect(evaluationMessage({ ...found, evaluation: { ...found.evaluation!, state: "dropped_budget" } })).toMatch(/size budget/);
  });
});

describe("similarity and timing", () => {
  it("keeps the coefficient's sign and labels interval overlaps", () => {
    expect(similarityText(comparison({ coefficient: 0.83, direction: "same" }))).toBe("moves together · r = +0.83");
    expect(similarityText(comparison({ coefficient: -0.71, direction: "opposite" }))).toBe("opposite direction · r = −0.71");
    expect(similarityText(comparison({ coefficient: null, direction: null, overlap_steps: 3 }))).toBe("unusual at the same time · 3 steps");
  });

  it("keeps the observed onset apart from the estimated lag", () => {
    expect(timingText(comparison({ onset_lead_seconds: 600, timing: "simultaneous", method: "deviation_correlation" }))).toBe(
      "began about 10 min earlier · no clear lead or lag",
    );
    expect(timingText(comparison({ onset_lead_seconds: -900, timing: "candidate_lags", lag_seconds: -900 }))).toBe(
      "began about 15 min later · best match shifted 15 min later",
    );
    expect(timingText(comparison({ onset_lead_seconds: null, timing: "inconclusive" }))).toBe("no change onset near the finding · order unclear");
  });
});

describe("pairRows", () => {
  it("aligns both series on the window's steps and keeps gaps", () => {
    const c = relatedFor(findingDetail(report, errors.finding_id)).comparisons[0]!;
    const charts = new Map(report.related_metrics!.charts.map((x) => [x.chart_id, x]));
    const anchor = charts.get(c.anchor.chart_id)!;
    const candidate = charts.get(c.candidate.chart_id)!;

    const rows = pairRows(anchor, { ...candidate, series: { ...candidate.series, values: candidate.series.values.map((v, i) => (i === 0 ? null : v)) } });

    expect(rows).toHaveLength(anchor.series.values.length);
    expect(rows[0]![3]).toBe("—");
  });
});

describe("relatedPairOption", () => {
  it("opens zoomed to the episode ± 15 min with the whole window reachable", () => {
    const c = relatedFor(findingDetail(report, errors.finding_id)).comparisons[0]!;
    const charts = new Map(report.related_metrics!.charts.map((x) => [x.chart_id, x]));
    const anchor = charts.get(c.anchor.chart_id)!;
    const candidate = charts.get(c.candidate.chart_id)!;
    const categories = timestamps(anchor);
    const minutes = (t: string) => new Date(t).getTime() / 60_000;

    const option = relatedPairOption(c, anchor, candidate, "values", palette, errors) as { dataZoom: { xAxisIndex: number[]; startValue: number; endValue: number }[] };

    for (const zoom of option.dataZoom) {
      expect(zoom.xAxisIndex).toEqual([0, 1]);
      expect(minutes(errors.start) - minutes(categories[zoom.startValue]!)).toBe(15);
      expect(minutes(categories[zoom.endValue]!) - minutes(errors.end)).toBe(15);
    }
    expect(categories.length).toBeGreaterThan(option.dataZoom[0]!.endValue - option.dataZoom[0]!.startValue + 1);
  });
});
