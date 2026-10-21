import type { Finding, MetricCapability, SignalCoverage, SourceKind } from "../api/client";
import { FAMILY_LABELS, formatDuration, formatValue } from "./format";

/** The 14-day trend window minus the latest day. */
const EARLIER_DAY_COUNT = 13;
/** Below this, "N× the usual" reads as noise rather than a meaningful jump. */
const MIN_MEANINGFUL_RATIO = 1.5;
const HOURS_PER_DAY = 24;
const MINUTES_PER_HOUR = 60;
const MS_PER_HOUR = 3_600_000;

const RECURRENCE_LABELS: Record<Exclude<Finding["recurrence"], "new">, string> = {
  recurring: "Recurring",
  repeated: "Seen before",
};

/** The normal range, or the heuristic for absolute-only findings. */
export function usualText(finding: Finding): string {
  const { expected, threshold, observed } = finding;
  if (expected) return `usual ${formatValue(expected.lower, expected.unit)}–${formatValue(expected.upper, expected.unit)}`;
  if (threshold !== null) return `heuristic ${formatValue(threshold, observed.unit)}`;
  return "";
}

export function recurrenceText(finding: Finding): string {
  if (finding.recurrence === "new") return "New today";

  return `${RECURRENCE_LABELS[finding.recurrence]} (${finding.prior_episode_days} of ${EARLIER_DAY_COUNT} earlier days)`;
}

/** One-line statement of a finding for headlines: "CPU on node · x peaked at 96.3 % over 3 h". */
export function headline(finding: Finding): string {
  const family = FAMILY_LABELS[finding.family] ?? finding.family;
  const peak = formatValue(finding.observed.value, finding.observed.unit);
  return `${family} on ${finding.entity.display_name} peaked at ${peak} over ${formatDuration(finding.duration_seconds)}`;
}

/** "That's 4.4× the usual 22.0 %." — only when the ratio is meaningful. */
export function ratioText(finding: Finding): string {
  const { observed, threshold } = finding;
  const median = finding.expected?.median;

  if (median && median > 0 && observed.value / median >= MIN_MEANINGFUL_RATIO) {
    return `That's ${(observed.value / median).toFixed(1)}× the usual ${formatValue(median, observed.unit)}.`;
  }
  if (threshold !== null) return `Above the ${formatValue(threshold, observed.unit)} diagnostic heuristic (not an SLO).`;
  return "";
}

/** The longest baseline any signal family used (0 without one); families with less history say so in Coverage. */
export const longestBaselineDays = (coverage: SignalCoverage[]) => Math.max(0, ...coverage.map((c) => c.baseline_days ?? 0));

/** Pairs of findings whose episodes overlap in time; overlap alone does not make them related. */
export function timeOverlaps(findings: Finding[]): [Finding, Finding][] {
  const pairs: [Finding, Finding][] = [];
  findings.forEach((a, i) => {
    for (const b of findings.slice(i + 1)) {
      if (a.start < b.end && b.start < a.end) pairs.push([a, b]);
    }
  });
  return pairs;
}

/** Each family comes from exactly one source kind; the report's capabilities say which. */
export function sourceByFamily(capabilities: MetricCapability[]): Map<Finding["family"], SourceKind> {
  return new Map(capabilities.map((c) => [c.family, c.source]));
}

const hoursBetween = (from: string, to: string) => (Date.parse(to) - Date.parse(from)) / MS_PER_HOUR;

/** The episode as a [from, to] span on the analysed day's 0–24 axis, where 24 is the report's end time. */
export function windowSpan(finding: Finding, end: string): [number, number] {
  const clamp = (hours: number) => Math.min(HOURS_PER_DAY, Math.max(0, HOURS_PER_DAY - hours));
  return [clamp(hoursBetween(finding.start, end)), clamp(hoursBetween(finding.end, end))];
}

/** "35 min" or "5 h": a time before the report's end, rounded to what a glance needs. */
function agoText(hours: number): string {
  return hours < 1 ? `${Math.round(hours * MINUTES_PER_HOUR)} min` : `${Math.round(hours)} h`;
}

/** "19 h → 4 h ago" or "3 h ago → now": when an episode ran, relative to the report's end time. */
export function agoRangeText(finding: Finding, end: string): string {
  const from = agoText(hoursBetween(finding.start, end));
  const toHours = hoursBetween(finding.end, end);
  if (toHours < 1 / MINUTES_PER_HOUR) return `${from} ago → now`;

  const to = agoText(toHours);
  return from === to ? `${from} ago` : `${from} → ${to} ago`;
}
