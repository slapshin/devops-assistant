import type { ConnectionTest, SourceKind } from "../../api/client";

export type HealthKey = "ok" | "checking" | "unreachable" | "auth" | "no_series" | "no_traffic" | "unreadable" | "error";

export const HEALTH_LABELS: Record<HealthKey, { label: string; icon: string }> = {
  ok: { label: "Reachable", icon: "✓" },
  checking: { label: "Checking…", icon: "◌" },
  unreachable: { label: "Unreachable", icon: "!" },
  auth: { label: "Auth failed", icon: "!" },
  no_series: { label: "No matching series", icon: "◐" },
  no_traffic: { label: "No traffic", icon: "◐" },
  unreadable: { label: "Credentials unreadable", icon: "!" },
  error: { label: "Check failed", icon: "!" },
};

export function testKey(test: ConnectionTest): HealthKey {
  if (!test.reachable) return "unreachable";
  if (test.auth_ok === false) return "auth";
  // A quiet application or host legitimately reports no Sentry events or Wazuh alerts, so only
  // metrics and edge warn.
  if (test.matched_series === 0 && test.kind !== "sentry" && test.kind !== "wazuh") return test.kind === "cloudflare" ? "no_traffic" : "no_series";
  return "ok";
}

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  prometheus: "Prometheus",
  cloudflare: "Cloudflare",
  sentry: "Sentry",
  wazuh: "Wazuh",
  grafana: "Grafana",
};

export const sourceKindLabel = (kind: SourceKind) => SOURCE_KIND_LABELS[kind];

/** What a connection test counts: matching series (Prometheus), requests (Cloudflare), events (Sentry) or alerts (Wazuh) in 24 h,
 * or current alert instances (Grafana). */
export function testVolume(test: ConnectionTest): string | null {
  if (test.matched_series === null || test.matched_series === undefined) return null;
  if (test.kind === "cloudflare") return `${test.matched_series.toLocaleString("en")} requests in 24 h`;
  if (test.kind === "sentry") return `${test.matched_series.toLocaleString("en")} events in 24 h`;
  if (test.kind === "wazuh") return `${test.matched_series.toLocaleString("en")} alerts in 24 h`;
  if (test.kind === "grafana") return `${test.matched_series.toLocaleString("en")} matching alert instances`;
  return `${test.matched_series} matching series`;
}
