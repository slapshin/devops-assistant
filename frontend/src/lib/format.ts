import { ref, watch } from "vue";

export type Unit = "ratio" | "bytes" | "bytes_per_second" | "requests_per_second" | "per_second" | "seconds" | "count";

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (unit) {
    case "ratio":
      return `${(value * 100).toFixed(value < 0.01 && value > 0 ? 2 : 1)} %`;
    case "seconds":
      return value < 1 ? `${(value * 1000).toFixed(0)} ms` : `${value.toFixed(2)} s`;
    case "bytes":
      return bytes(value);
    case "bytes_per_second":
      return `${bytes(value)}/s`;
    case "requests_per_second":
      return `${value.toFixed(2)} req/s`;
    case "per_second":
      return `${value.toPrecision(3)}/s`;
    default:
      return value.toPrecision(3);
  }
}

function bytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = value;
  let i = 0;
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

const TZ_KEY = "assistant.timezone";
function readTz(): "utc" | "local" {
  try {
    return localStorage.getItem(TZ_KEY) === "local" ? "local" : "utc";
  } catch {
    return "utc";
  }
}
/** UTC by default (UI_SPEC §2); the choice is a per-viewer convenience. */
export const timezone = ref<"utc" | "local">(readTz());
watch(timezone, (tz) => {
  try {
    localStorage.setItem(TZ_KEY, tz);
  } catch {
    /* storage unavailable: keep in memory */
  }
});

export function formatTime(iso: string, withDate = true): string {
  const d = new Date(iso);
  const opts: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...(withDate ? { day: "2-digit", month: "short" } : {}),
    ...(timezone.value === "utc" ? { timeZone: "UTC" } : {}),
  };
  return `${new Intl.DateTimeFormat("en-GB", opts).format(d)}${timezone.value === "utc" ? " UTC" : ""}`;
}

export const utcTooltip = (iso: string) => new Date(iso).toISOString().replace(".000Z", "Z");

export function formatDuration(seconds: number): string {
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;
export const severityRank = (s: string) => SEVERITIES.indexOf(s as (typeof SEVERITIES)[number]);

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
};

export const STATUS_LABELS: Record<string, { label: string; icon: string }> = {
  anomalous: { label: "Anomalous", icon: "▲" },
  no_anomaly: { label: "No anomaly", icon: "✓" },
  insufficient_data: { label: "Insufficient data", icon: "◐" },
  unsupported: { label: "Unsupported", icon: "⊘" },
  source_error: { label: "Source error", icon: "!" },
  not_evaluated: { label: "Not evaluated", icon: "–" },
};

export const JOB_STATES: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function isLatencyMean(signal: string): boolean {
  return signal === "latency_mean";
}
