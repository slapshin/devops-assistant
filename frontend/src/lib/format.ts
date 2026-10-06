import { ref, watch } from "vue";

const MS_PER_SECOND = 1000;
const US_PER_SECOND = 1_000_000;
/** Below this, durations are shown in µs (Redis commands run in microseconds). */
const SUB_MILLISECOND = 0.001;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;
const BYTES_PER_KIBIBYTE = 1024;
const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB"];
/** Ratios below this (but above zero) get an extra decimal so they do not round to 0.0 %. */
const SMALL_RATIO = 0.01;
const TIMEZONE_STORAGE_KEY = "assistant.timezone";
const NO_VALUE = "—";

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_VALUE;

  switch (unit) {
    case "ratio":
      return `${(value * 100).toFixed(value < SMALL_RATIO && value > 0 ? 2 : 1)} %`;
    case "seconds":
      if (value > 0 && value < SUB_MILLISECOND) return `${(value * US_PER_SECOND).toFixed(0)} µs`;
      return value < 1 ? `${(value * MS_PER_SECOND).toFixed(0)} ms` : `${value.toFixed(2)} s`;
    case "bytes":
      return formatBytes(value);
    case "bytes_per_second":
      return `${formatBytes(value)}/s`;
    case "requests_per_second":
      return `${value.toFixed(2)} req/s`;
    case "per_second":
      return `${value.toPrecision(3)}/s`;
    default:
      return value.toPrecision(3);
  }
}

function formatBytes(value: number): string {
  let scaled = value;
  let unitIndex = 0;
  while (Math.abs(scaled) >= BYTES_PER_KIBIBYTE && unitIndex < BYTE_UNITS.length - 1) {
    scaled /= BYTES_PER_KIBIBYTE;
    unitIndex++;
  }

  return `${scaled.toFixed(1)} ${BYTE_UNITS[unitIndex]}`;
}

type TimezoneChoice = "utc" | "local";

function readTimezone(): TimezoneChoice {
  try {
    return localStorage.getItem(TIMEZONE_STORAGE_KEY) === "utc" ? "utc" : "local";
  } catch {
    return "local";
  }
}

/** Local time by default (UI_SPEC §2); the choice is a per-viewer convenience. */
export const timezone = ref<TimezoneChoice>(readTimezone());

watch(timezone, (choice) => {
  try {
    localStorage.setItem(TIMEZONE_STORAGE_KEY, choice);
  } catch {
    /* storage unavailable: keep in memory */
  }
});

/** `withZone` appends " UTC" in UTC mode; drop it where the zone is shown once for a group of times. */
export function formatTime(iso: string, withDate = true, withZone = true): string {
  const isUtc = timezone.value === "utc";
  const options: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(withDate ? { day: "2-digit", month: "short" } : {}),
    ...(isUtc ? { timeZone: "UTC" } : {}),
  };

  return `${new Intl.DateTimeFormat("en-GB", options).format(new Date(iso))}${isUtc && withZone ? " UTC" : ""}`;
}

/** Same RFC 3339 form as the API ("…:00Z"), without milliseconds. */
export const toRfc3339 = (date: Date) => date.toISOString().replace(".000Z", "Z");

export const utcTooltip = (iso: string) => toRfc3339(new Date(iso));

export function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / SECONDS_PER_MINUTE);
  if (totalMinutes < MINUTES_PER_HOUR) return `${totalMinutes} min`;

  const hours = Math.floor(totalMinutes / MINUTES_PER_HOUR);
  const minutes = totalMinutes % MINUTES_PER_HOUR;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

export const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;

export const FAMILY_LABELS: Record<string, string> = {
  cpu: "CPU",
  memory: "Memory",
  filesystem: "Filesystem",
  disk_io: "Disk I/O",
  network: "Network",
  container: "Containers",
  request_traffic: "HTTP/RPC traffic",
  request_failures: "Failures",
  client_errors: "Client errors",
  latency: "Latency",
  proxy: "Reverse proxies",
  database: "Databases",
  edge: "CDN edge (Cloudflare)",
  security: "Security (WAF)",
};

export const STATUS_LABELS: Record<string, { label: string; icon: string }> = {
  anomalous: { label: "Anomalous", icon: "▲" },
  no_anomaly: { label: "No anomaly", icon: "✓" },
  insufficient_data: { label: "Insufficient data", icon: "◐" },
  unsupported: { label: "Unsupported", icon: "⊘" },
  source_error: { label: "Source error", icon: "!" },
  not_evaluated: { label: "Not evaluated", icon: "–" },
};

export const STAGE_LABELS: Record<string, string> = {
  discovery: "Discover capabilities",
  collection: "Collect metrics",
  detection: "Detect anomalies",
  trends: "Build 14-day trends",
  explanation: "AI explanation",
  saving: "Save report",
};

export const CAPABILITY_LABELS: Record<string, { label: string; icon: string }> = {
  supported: { label: "Supported", icon: "✓" },
  partial: { label: "Partial", icon: "◐" },
  unverified: { label: "Unverified", icon: "?" },
  unsupported: { label: "Unsupported", icon: "⊘" },
};

/** "3 h ago"-style age for list pages; the absolute time goes in a tooltip. */
export function formatAge(iso: string, now = Date.now()): string {
  const minutes = Math.round((now - new Date(iso).getTime()) / (MS_PER_SECOND * SECONDS_PER_MINUTE));
  if (minutes < 1) return "just now";
  if (minutes < MINUTES_PER_HOUR) return `${minutes} min ago`;
  const hours = Math.round(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return `${hours} h ago`;
  return `${Math.round(hours / HOURS_PER_DAY)} d ago`;
}

export const JOB_STATES: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Mean latency comes from _sum/_count because no histogram buckets exist, so no percentiles. */
export function isLatencyMean(signal: string): boolean {
  return signal === "latency_mean";
}
