import type { DailyTrend, Evidence, Finding } from "../api/client";
import { formatTime, formatValue, toRfc3339 } from "./format";
import type { ChartPalette } from "./theme";

const MS_PER_SECOND = 1000;

export function timestamps(evidence: Evidence): string[] {
  const startMs = new Date(evidence.series.start).getTime();
  const stepMs = evidence.series.step_seconds * MS_PER_SECOND;
  // Same RFC 3339 form as the API, so markArea bounds match category values exactly.
  return evidence.series.values.map((_, i) => toRfc3339(new Date(startMs + i * stepMs)));
}

/** Nearest category at or before t (episode end is exclusive and may equal the window end). */
function nearestCategoryAtOrBefore(categories: string[], t: string): string {
  let best = categories[0] ?? t;
  for (const category of categories) if (category <= t) best = category;
  return best;
}

function bandSeries(evidence: Evidence, palette: ChartPalette): Record<string, unknown>[] {
  if (!evidence.lower || !evidence.upper) return [];

  const lower = evidence.lower;
  // Stacked on top of the invisible lower line, so the area spans lower..upper.
  const bandHeight = evidence.upper.map((upper, i) => {
    const low = lower[i];
    return upper === null || low === null || low === undefined ? null : upper - low;
  });
  return [
    { name: "lower", type: "line", data: lower, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
    { name: "Expected range", type: "line", data: bandHeight, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, itemStyle: { color: palette.band }, areaStyle: { color: palette.band, opacity: 1 }, silent: true },
  ];
}

function thresholdMarkLine(threshold: number | null | undefined, palette: ChartPalette) {
  if (threshold === null || threshold === undefined) return undefined;
  return {
    symbol: "none",
    label: { formatter: "heuristic (not an SLO)", position: "insideEndTop", color: palette.threshold },
    lineStyle: { type: "dashed", color: palette.threshold },
    data: [{ yAxis: threshold }],
  };
}

function ariaDescription(evidence: Evidence, finding: Finding): string {
  const unit = evidence.series.unit;
  const gapCount = evidence.series.values.filter((v) => v === null).length;
  const expected = finding.expected ? `, expected ${formatValue(finding.expected.median, unit)}` : "";
  return `${finding.title} on ${finding.entity.display_name}: observed peak ${formatValue(finding.observed.value, unit)}` +
    `${expected}; ${gapCount} gap steps. Use "Show data" for values.`;
}

/** Observed series with gaps as breaks, expected band, heuristic line, and episode span. */
export function evidenceOption(evidence: Evidence, finding: Finding, palette: ChartPalette): Record<string, unknown> {
  const unit = evidence.series.unit;
  const categories = timestamps(evidence);

  const series: Record<string, unknown>[] = bandSeries(evidence, palette);
  if (evidence.expected) {
    series.push({ name: "Expected (median)", type: "line", data: evidence.expected, symbol: "none", itemStyle: { color: palette.expected }, lineStyle: { type: "dashed", width: 1, color: palette.expected } });
  }
  series.push({
    name: "Observed",
    type: "line",
    data: evidence.series.values,
    symbol: "none",
    connectNulls: false,
    itemStyle: { color: palette.observed },
    lineStyle: { width: 2, color: palette.observed },
    markArea: {
      silent: true,
      itemStyle: { color: palette.episode, opacity: 1 },
      label: { show: false },
      data: [[
        { name: "Episode", xAxis: nearestCategoryAtOrBefore(categories, finding.start) },
        { xAxis: nearestCategoryAtOrBefore(categories, finding.end) },
      ]],
    },
    markLine: thresholdMarkLine(evidence.threshold, palette),
  });

  return {
    aria: { enabled: true, label: { description: ariaDescription(evidence, finding) } },
    animation: false,
    grid: { left: 70, right: 20, top: 30, bottom: 60 },
    textStyle: { color: palette.muted },
    legend: { top: 0, data: ["Observed", "Expected (median)", "Expected range"], textStyle: { color: palette.text } },
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.bg,
      borderColor: palette.border,
      textStyle: { color: palette.text },
      valueFormatter: (v: number | null) => (v === null || v === undefined ? "gap" : formatValue(v, unit)),
    },
    xAxis: {
      type: "category", data: categories,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, formatter: (v: string) => formatTime(v, false) },
    },
    yAxis: {
      type: "value", scale: true,
      splitLine: { lineStyle: { color: palette.border, opacity: 0.6 } },
      axisLabel: { color: palette.muted, formatter: (v: number) => formatValue(v, unit) },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, borderColor: palette.border, textStyle: { color: palette.muted } }],
    series,
  };
}

export const MEASURES = {
  anomalous_share: { label: "Anomalous share", format: (t: DailyTrend) => (t.anomalous_share === null ? null : t.anomalous_share * 100), unit: "%" },
  episode_count: { label: "Episodes", format: (t: DailyTrend) => t.episode_count, unit: "" },
  anomalous_minutes: { label: "Anomalous entity-minutes", format: (t: DailyTrend) => t.anomalous_minutes, unit: "min" },
  affected_entities: { label: "Affected entities", format: (t: DailyTrend) => t.affected_entities.length, unit: "" },
} as const;
export type Measure = keyof typeof MEASURES;

/** "14 Mar" from the day window's end. */
export function dayLabel(trend: { window: { end: string } }): string {
  return formatTime(trend.window.end).split(" ").slice(0, 2).join(" ").replace(/,$/, "");
}
