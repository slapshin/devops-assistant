import type { FindingDetail, FindingRelatedMetrics, RelatedChart, RelatedEvaluation, RelatedMetric } from "../api/client";
import { timestamps } from "./charts";
import { formatDuration, formatTime, formatValue } from "./format";
import type { ChartPalette } from "./theme";

const SECONDS_PER_MINUTE = 60;
const PERCENT = 100;
const MS_PER_MINUTE = 60_000;
/** The pair chart opens on the finding's episode ± this; the rest of the window is a zoom-out away. */
export const RELATED_ZOOM_PAD_MINUTES = 15;
/** Rows shown before "Show all". */
export const RELATED_DEFAULT_ROWS = 3;

export type RelatedView = "values" | "deviation";

export interface FindingRelated {
  /** Null when the report predates related metrics (schema < 2.9). */
  related: FindingRelatedMetrics | null;
  evaluation: RelatedEvaluation | null;
  comparisons: RelatedMetric[];
}

export function relatedFor(detail: FindingDetail): FindingRelated {
  const related = detail.related ?? null;
  if (!related) return { related: null, evaluation: null, comparisons: [] };

  return { related, evaluation: related.evaluation ?? null, comparisons: [...related.comparisons].sort((a, b) => a.rank - b.rank) };
}

export const ASSOCIATION_LABELS: Record<RelatedMetric["association"], string> = {
  same_entity: "Same resource",
  same_service: "Same service",
  same_database_server: "Same database server",
  same_storage_cluster: "Same storage cluster",
  shared_host: "Shared host",
  unknown: "Unknown association",
};

export const isVerified = (c: RelatedMetric) => c.association !== "unknown";

/** "moves together · r = +0.83"; interval comparisons have no coefficient. */
export function similarityText(c: RelatedMetric): string {
  if (c.coefficient === null || c.coefficient === undefined) return `unusual at the same time · ${c.overlap_steps} steps`;

  const sign = c.coefficient >= 0 ? "+" : "−";
  const moves = c.direction === "opposite" ? "opposite direction" : "moves together";
  return `${moves} · r = ${sign}${Math.abs(c.coefficient).toFixed(2)}`;
}

function minutes(seconds: number): string {
  return formatDuration(Math.abs(seconds));
}

/** Observed onset difference, then the estimated lag; both are approximate at 5-min steps. */
export function timingText(c: RelatedMetric): string {
  const parts: string[] = [];
  const lead = c.onset_lead_seconds;
  if (lead === null || lead === undefined) parts.push("no change onset near the finding");
  else if (lead === 0) parts.push("began in the same step");
  else parts.push(`began about ${minutes(lead)} ${lead > 0 ? "earlier" : "later"}`);

  switch (c.timing) {
    case "candidate_leads":
      parts.push(`best match shifted ${minutes(c.lag_seconds ?? 0)} earlier`);
      break;
    case "candidate_lags":
      parts.push(`best match shifted ${minutes(c.lag_seconds ?? 0)} later`);
      break;
    case "inconclusive":
      parts.push("order unclear");
      break;
    default:
      if (c.method === "deviation_correlation") parts.push("no clear lead or lag");
  }
  return parts.join(" · ");
}

export function qualityText(c: RelatedMetric): string {
  return `${Math.round(c.paired_coverage * PERCENT)} % paired`;
}

/** Why there is nothing to show (or what was left out), never "healthy". */
export function evaluationMessage(found: FindingRelated): string | null {
  const e = found.evaluation;
  if (!found.related) return "This report predates related metrics. Run a new analysis to compare other metrics with this finding.";
  if (!e) return "This finding was not compared with other metrics.";

  switch (e.state) {
    case "insufficient_data":
      return `Not compared: ${(e.reasons ?? []).map((r) => r.message).join(" ")}`;
    case "skipped_budget":
      return "Not compared: the run's comparison budget was used by more severe findings.";
    case "dropped_budget":
      return "Comparisons were computed but dropped to keep the report within its size budget.";
    default:
      if (e.retained > 0) return null;
      if (e.candidates === 0) return "No other collected metric had an unusual change around this finding.";
      return "No other collected metric changed in a similar way around this finding.";
  }
}

/** One line of what was considered, so an empty list never reads as "nothing else changed". */
export function evaluationCounts(e: RelatedEvaluation): string {
  const parts = [`${e.candidates} with an unusual change in the window`, `${e.evaluated} compared`];
  if (e.omitted) parts.push(`${e.omitted} more qualifying not kept`);
  if (e.not_evaluated) parts.push(`${e.not_evaluated} not compared (limit)`);
  if (e.shared_input) parts.push(`${e.shared_input} computed from the same data, skipped`);
  for (const u of e.unavailable ?? []) parts.push(`${u.count} unavailable (${u.code.replaceAll("_", " ")})`);
  return parts.join(" · ");
}

export function seriesTitle(side: RelatedMetric["anchor"]): string {
  return `${side.label} · ${side.entity.display_name}`;
}

export const stepMinutes = (c: RelatedMetric) => Math.round(c.step_seconds / SECONDS_PER_MINUTE);

/** Time, both values in their own units, and their deviations; gaps stay gaps. */
export function pairRows(anchor: RelatedChart, candidate: RelatedChart): string[][] {
  const times = timestamps(anchor);
  const z = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(1));
  return times.map((t, i) => [
    formatTime(t),
    formatValue(anchor.series.values[i], anchor.series.unit),
    z(anchor.deviation?.[i]),
    formatValue(candidate.series.values[i], candidate.series.unit),
    z(candidate.deviation?.[i]),
  ]);
}

interface Side {
  chart: RelatedChart;
  title: string;
  color: string;
}

function valueText(unit: string, view: RelatedView) {
  return (v: number | null | undefined) => {
    if (v === null || v === undefined) return "gap";
    return view === "deviation" ? `z ${v.toFixed(1)}` : formatValue(v, unit);
  };
}

function sideSeries(side: Side, view: RelatedView, index: number, palette: ChartPalette): Record<string, unknown>[] {
  const data = view === "deviation" ? side.chart.deviation ?? side.chart.series.values.map(() => null) : side.chart.series.values;
  const out: Record<string, unknown>[] = [
    {
      name: side.title,
      type: "line",
      xAxisIndex: index,
      yAxisIndex: index,
      data,
      symbol: "none",
      connectNulls: false,
      itemStyle: { color: side.color },
      lineStyle: { width: 1.8, color: side.color },
      tooltip: { valueFormatter: valueText(side.chart.series.unit, view) },
    },
  ];
  if (view === "values" && side.chart.expected) {
    out.push({
      name: `${side.title} (usual)`,
      type: "line",
      xAxisIndex: index,
      yAxisIndex: index,
      data: side.chart.expected,
      symbol: "none",
      connectNulls: false,
      itemStyle: { color: palette.muted },
      lineStyle: { type: "dashed", width: 1, color: palette.muted },
      tooltip: { valueFormatter: valueText(side.chart.series.unit, view) },
    });
  }
  return out;
}

/** Two stacked charts on one shared time axis with a linked pointer, each in its own unit (or both
 * as deviations from usual on a labelled robust-z scale). Charts stay on actual timestamps. */
export function relatedPairOption(
  c: RelatedMetric,
  anchor: RelatedChart,
  candidate: RelatedChart,
  view: RelatedView,
  palette: ChartPalette,
  finding: { start: string; end: string; severity: string },
): Record<string, unknown> {
  const categories = timestamps(anchor);
  const sides: Side[] = [
    { chart: anchor, title: `${c.anchor.label} (finding)`, color: palette.observed },
    { chart: candidate, title: c.candidate.label, color: palette.severity.high ?? palette.text },
  ];
  const episodeColor = palette.severity[finding.severity] ?? palette.muted;
  const first = (t: string) => categories.find((x) => x >= t) ?? categories[categories.length - 1];
  const units = sides.map((s) => s.chart.series.unit);
  // Initial zoom by step index (inclusive), so it never depends on category string formats.
  const padMs = RELATED_ZOOM_PAD_MINUTES * MS_PER_MINUTE;
  const times = categories.map((t) => new Date(t).getTime());
  const from = new Date(finding.start).getTime() - padMs;
  const to = new Date(finding.end).getTime() + padMs;
  const startIndex = Math.max(0, times.findIndex((t) => t >= from));
  const after = times.findIndex((t) => t > to);
  const endIndex = after === -1 ? categories.length - 1 : Math.max(startIndex, after - 1);
  const format = (i: number) => (v: number) => (view === "deviation" ? v.toFixed(1) : formatValue(v, units[i] ?? "count"));

  const xAxis = sides.map((_, i) => ({
    type: "category",
    gridIndex: i,
    data: categories,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: palette.border } },
    axisLabel: { show: i === 1, color: palette.muted, formatter: (v: string) => formatTime(v, false, false) },
    axisPointer: { label: { formatter: (p: { value: string }) => formatTime(p.value) } },
  }));
  const yAxis = sides.map((s, i) => ({
    type: "value",
    gridIndex: i,
    scale: true,
    name: view === "deviation" ? "robust z (deviation from usual, clipped ±10)" : s.title,
    nameTextStyle: { color: palette.muted, align: "left" },
    splitLine: { lineStyle: { color: palette.grid } },
    axisLabel: { color: palette.muted, formatter: format(i) },
  }));
  const series = sides.flatMap((s, i) => sideSeries(s, view, i, palette));
  // The finding's episode is tinted on both plots, labelled once.
  for (const [i, s] of series.entries()) {
    if (s.xAxisIndex === undefined || series.findIndex((x) => x.xAxisIndex === s.xAxisIndex) !== i) continue;
    s.markArea = {
      silent: true,
      itemStyle: { color: episodeColor, opacity: 0.12 },
      label: { show: s.xAxisIndex === 0 },
      data: [[{ name: "Finding", xAxis: first(finding.start) }, { xAxis: first(finding.end) }]],
    };
  }

  return {
    aria: {
      enabled: true,
      label: {
        description: `${seriesTitle(c.anchor)} and ${seriesTitle(c.candidate)} over ${formatTime(c.window.start)} – ${formatTime(c.window.end)}; ` +
          `${similarityText(c)}; ${timingText(c)}. Use "Show data" for values.`,
      },
    },
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 72, right: 16, top: 28, height: "31%" },
      { left: 72, right: 16, top: "52%", height: "31%" },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], startValue: startIndex, endValue: endIndex },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        startValue: startIndex,
        endValue: endIndex,
        height: 18,
        bottom: 8,
        borderColor: palette.border,
        fillerColor: palette.band,
        textStyle: { color: palette.muted },
        labelFormatter: (i: number) => formatTime(categories[i] ?? "", false, false),
      },
    ],
    textStyle: { color: palette.muted, fontFamily: palette.font },
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.bg,
      borderColor: palette.border,
      textStyle: { color: palette.text },
    },
    xAxis,
    yAxis,
    series,
  };
}
