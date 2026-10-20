import { afterEach, describe, expect, it } from "vitest";

import type { Evidence, Finding } from "../api/client";
import { evidenceOption } from "./charts";
import { formatTime, timezone } from "./format";
import type { ChartPalette } from "./theme";

const palette: ChartPalette = {
  font: "sans-serif", bg: "#fff", text: "#000", muted: "#666", border: "#ccc", grid: "#eee",
  observed: "#00f", expected: "#00f", band: "#ddf", severity: {},
};
const evidence = { series: { start: "2026-10-19T09:15:00Z", step_seconds: 300, unit: "bytes", values: [1, 2] } } as unknown as Evidence;
const finding = {
  title: "Memory", severity: "high", start: "2026-10-19T09:15:00Z", end: "2026-10-19T09:20:00Z",
  entity: { display_name: "filer" }, observed: { value: 2 }, expected: null,
} as unknown as Finding;

function tooltipHeader(value: string): string {
  const option = evidenceOption(evidence, finding, palette) as { xAxis: { axisPointer: { label: { formatter: (p: { value: string }) => string } } } };
  return option.xAxis.axisPointer.label.formatter({ value });
}

describe("evidenceOption", () => {
  afterEach(() => {
    timezone.value = "local";
  });

  it("formats the tooltip header in local time like the axis", () => {
    expect(tooltipHeader("2026-10-19T09:15:00Z")).toBe(formatTime("2026-10-19T09:15:00Z"));
  });

  it("formats the tooltip header in UTC when UTC is chosen", () => {
    timezone.value = "utc";

    const header = tooltipHeader("2026-10-19T09:15:00Z");

    expect(header).toBe("19 Oct, 09:15 UTC");
  });
});
