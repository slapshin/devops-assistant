import type { AlertInstanceActivity, Finding } from "../api/client";

/** Labels Grafana adds to every instance of a rule; they never tell instances apart. */
const RULE_LABELS = new Set(["alertname", "grafana_folder"]);
/** Labels that usually name the affected thing, for when a single instance has nothing to compare. */
const NAME_LABELS = ["instance", "host", "hostname", "nodename", "node", "pod", "container", "service", "job"];
const SHOWN_IN_ROW = 2;

/** Label names whose values differ between the instances, or the instance's naming label when alone. */
export function distinguishingLabels(instances: AlertInstanceActivity[]): string[] {
  const names = [...new Set(instances.flatMap((i) => Object.keys(i.labels)))].filter((n) => !RULE_LABELS.has(n)).sort();
  const differing = names.filter((n) => new Set(instances.map((i) => i.labels[n] ?? "")).size > 1);
  if (differing.length) return differing;

  const named = NAME_LABELS.find((n) => instances.some((i) => i.labels[n]));
  return named ? [named] : [];
}

/** "gpu-hive-4" for one distinguishing label, "env=prod, pod=x" for several. */
export function instanceName(instance: AlertInstanceActivity, keys: string[]): string {
  const present = keys.filter((k) => instance.labels[k] !== undefined);
  if (present.length === 1) return instance.labels[present[0] as string] ?? "";

  return present.map((k) => `${k}=${instance.labels[k]}`).join(", ");
}

/** Short list of the instances behind a finding for a table cell: "gpu-hive-4, gpu-lndvps-12 +1". */
export function instancesText(finding: Finding): string {
  const instances = finding.alert_instances ?? [];
  const keys = distinguishingLabels(instances);
  const names = instances.map((i) => instanceName(i, keys)).filter((n) => n);
  if (!names.length) return "";

  const more = names.length - SHOWN_IN_ROW;
  return names.slice(0, SHOWN_IN_ROW).join(", ") + (more > 0 ? ` +${more}` : "");
}
