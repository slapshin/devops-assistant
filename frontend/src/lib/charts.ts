import type { DailyTrend, Evidence, Finding } from "../api/client";
import { formatTime, formatValue } from "./format";

export function timestamps(evidence: Evidence): string[] {
  const start = new Date(evidence.series.start).getTime();
  // Same RFC 3339 form as the API ("…:00Z"), so markArea bounds match category values exactly.
  return evidence.series.values.map((_, i) =>
    new Date(start + i * evidence.series.step_seconds * 1000).toISOString().replace(".000Z", "Z"),
  );
}

/** Nearest category at or before t (episode end is exclusive and may equal the window end). */
function clamp(xs: string[], t: string): string {
  let best = xs[0] ?? t;
  for (const x of xs) if (x <= t) best = x;
  return best;
}

/** Observed series with gaps as breaks, expected band, heuristic line, and episode span. */
export function evidenceOption(evidence: Evidence, finding: Finding): Record<string, unknown> {
  const unit = evidence.series.unit;
  const xs = timestamps(evidence);
  const lower = evidence.lower ?? [];
  const upper = evidence.upper ?? [];
  const band = upper.map((u, i) => (u === null || lower[i] === null || lower[i] === undefined ? null : u - (lower[i] as number)));
  const series: Record<string, unknown>[] = [];
  if (evidence.lower && evidence.upper) {
    series.push(
      { name: "lower", type: "line", data: lower, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { name: "Expected range", type: "line", data: band, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { opacity: 0.18 }, silent: true },
    );
  }
  if (evidence.expected) {
    series.push({ name: "Expected (median)", type: "line", data: evidence.expected, symbol: "none", lineStyle: { type: "dashed", width: 1 } });
  }
  series.push({
    name: "Observed",
    type: "line",
    data: evidence.series.values,
    symbol: "none",
    connectNulls: false,
    lineStyle: { width: 2 },
    markArea: {
      silent: true,
      itemStyle: { opacity: 0.12 },
      label: { show: false },
      data: [[{ name: "Episode", xAxis: clamp(xs, finding.start) }, { xAxis: clamp(xs, finding.end) }]],
    },
    markLine:
      evidence.threshold !== null && evidence.threshold !== undefined
        ? { symbol: "none", label: { formatter: "heuristic" }, lineStyle: { type: "dotted" }, data: [{ yAxis: evidence.threshold }] }
        : undefined,
  });
  const gaps = evidence.series.values.filter((v) => v === null).length;
  return {
    aria: {
      enabled: true,
      label: {
        description: `${finding.title} on ${finding.entity.display_name}: observed peak ${formatValue(finding.observed.value, unit)}` +
          `${finding.expected ? `, expected ${formatValue(finding.expected.median, unit)}` : ""}; ${gaps} gap steps. Use "Show data" for values.`,
      },
    },
    animation: false,
    grid: { left: 70, right: 20, top: 30, bottom: 60 },
    legend: { top: 0, data: ["Observed", "Expected (median)", "Expected range"] },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => (v === null || v === undefined ? "gap" : formatValue(v, unit)),
    },
    xAxis: { type: "category", data: xs, axisLabel: { formatter: (v: string) => formatTime(v, false) } },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: (v: number) => formatValue(v, unit) } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
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

export function trendOption(trends: DailyTrend[], measure: Measure, selected: number | null): Record<string, unknown> {
  const days = [...trends].sort((a, b) => b.bucket_index - a.bucket_index); // oldest → newest
  const m = MEASURES[measure];
  return {
    aria: { enabled: true, label: { description: `${m.label} per day for 14 days. Use the day buttons or "Show data" for values.` } },
    animation: false,
    grid: [
      { left: 60, right: 20, top: 20, height: "60%" },
      { left: 60, right: 20, bottom: 30, height: "12%" },
    ],
    tooltip: { trigger: "axis" },
    xAxis: [
      { type: "category", data: days.map((d) => dayLabel(d)), gridIndex: 0 },
      { type: "category", data: days.map((d) => d.bucket_index), gridIndex: 1, show: false },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, name: m.unit },
      { type: "value", gridIndex: 1, max: 100, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: m.label,
        type: "bar",
        data: days.map((d) => ({
          value: d.status === "ok" ? m.format(d) : null,
          itemStyle: { opacity: selected === null || selected === d.bucket_index ? 1 : 0.4 },
        })),
        markArea: {
          silent: true,
          itemStyle: { color: "rgba(128,128,128,0.15)" },
          label: { show: true, position: "insideTop", fontSize: 9 },
          data: days
            .filter((d) => d.status !== "ok")
            .map((d) => {
              const name = d.status === "insufficient_baseline" ? "no baseline" : d.status === "insufficient_data" ? "no data" : "error";
              const x = dayLabel(d);
              return [{ name, xAxis: x }, { xAxis: x }];
            }),
        },
      },
      { name: "Coverage", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: days.map((d) => Math.round(d.coverage * 100)), barWidth: "60%" },
    ],
  };
}

export function dayLabel(t: { window: { end: string } }): string {
  return formatTime(t.window.end).split(" ").slice(0, 2).join(" ").replace(/,$/, "");
}
