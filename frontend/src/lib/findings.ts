import type { Finding, MetricCapability, SignalCoverage, SourceKind } from "../api/client";
import { FAMILY_LABELS, formatDuration, formatValue, timezone } from "./format";

/** The 14-day trend window minus the latest day. */
const EARLIER_DAY_COUNT = 13;
/** Below this, "N× the usual" reads as noise rather than a meaningful jump. */
const MIN_MEANINGFUL_RATIO = 1.5;
const HOURS_PER_DAY = 24;
const MS_PER_HOUR = 3_600_000;
/** Six-hour parts of the day, in order from midnight. */
const DAY_PARTS = ["Night", "Morning", "Afternoon", "Evening"] as const;
const HOURS_PER_DAY_PART = HOURS_PER_DAY / DAY_PARTS.length;

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

/** Hour of the day (fractional) in the viewer's chosen time zone. */
function hourOfDay(iso: string): number {
  const date = new Date(iso);
  return timezone.value === "utc"
    ? date.getUTCHours() + date.getUTCMinutes() / 60
    : date.getHours() + date.getMinutes() / 60;
}

const dayPart = (hour: number) => DAY_PARTS[Math.floor(hour / HOURS_PER_DAY_PART) % DAY_PARTS.length]!;

/** The hours of the day an episode covers, as [from, to] spans on a 0–24 axis; an episode over midnight wraps. */
export function daySpans(finding: Finding): [number, number][] {
  const hours = (Date.parse(finding.end) - Date.parse(finding.start)) / MS_PER_HOUR;
  if (hours >= HOURS_PER_DAY) return [[0, HOURS_PER_DAY]];

  const from = hourOfDay(finding.start);
  const to = from + hours;
  return to <= HOURS_PER_DAY ? [[from, to]] : [[from, HOURS_PER_DAY], [0, to - HOURS_PER_DAY]];
}

/** "Morning", "Evening → Night", or "All day": when in the day an episode happened. */
export function dayPartText(finding: Finding): string {
  const hours = (Date.parse(finding.end) - Date.parse(finding.start)) / MS_PER_HOUR;
  if (hours >= HOURS_PER_DAY) return "All day";

  const from = hourOfDay(finding.start);
  // A minute before the end, so an episode ending at 06:00 ends the night rather than starting the morning.
  const to = from + Math.max(0, hours - 1 / 60);
  const first = dayPart(from);
  const last = dayPart(to % HOURS_PER_DAY);
  return first === last ? first : `${first} → ${last}`;
}
