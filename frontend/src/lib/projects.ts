import type { LabelMatcher, ProjectInput, ProjectSummary, ReportSchedule, ReportSummary, SourceInfo, Weekday } from "../api/client";
import type { FieldErrors } from "./auth";
import { type NotificationDraft, notificationDraftFrom, notificationInput, validateNotifications } from "./notifications";
import { type SourceDraft, emptySourceDraft, sourceDraftFrom } from "./projectSources/draft";
import { cloudflareSource, sentrySource } from "./projectSources/draft";
import { prepareSourceSubmission, validateSources } from "./projectSources";

export type { FieldErrors } from "./auth";
export {
  MATCH_OPS,
  MAX_MATCHERS,
  MAX_GRAFANA_FOLDERS,
  MAX_GRAFANA_RULES,
  MAX_SENTRY_PROJECTS,
  MAX_SENTRY_TAGS,
  MAX_WAZUH_AGENTS,
  MAX_WAZUH_GROUPS,
  MAX_WAZUH_LABELS,
  SENTRY_URL,
  WAZUH_INDEX_PATTERN,
  WAZUH_MONITORING_INDEX_PATTERN,
  emptyMatcher,
  prometheusSource,
  cloudflareSource,
  sentrySource,
  wazuhSource,
  grafanaSource,
} from "./projectSources/draft";
export type { MatcherDraft, TagDraft, MatchOp } from "./projectSources/draft";

const MAX_NAME_CHARS = 100;
/** Mirrors backend/app/domain/schedule.py. */
const LOCAL_TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
export const WEEKDAYS: { day: Weekday; label: string }[] = [
  { day: "mon", label: "Mon" },
  { day: "tue", label: "Tue" },
  { day: "wed", label: "Wed" },
  { day: "thu", label: "Thu" },
  { day: "fri", label: "Fri" },
  { day: "sat", label: "Sat" },
  { day: "sun", label: "Sun" },
];
const ALL_DAYS = WEEKDAYS.map((w) => w.day);
const WORK_DAYS: Weekday[] = ["mon", "tue", "wed", "thu", "fri"];
const DEFAULT_SCHEDULE_TIME = "08:00";
/** Mirrors MAX_KEEP_REPORTS in backend/app/domain/projects.py. */
export const MAX_KEEP_REPORTS = 1000;
const DEFAULT_KEEP_REPORTS = 10;

/** The viewer's IANA zone, so "08:00" means their morning by default. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** IANA zones the browser knows, for suggestions; the server validates the final value. */
export function timezoneOptions(): string[] {
  try {
    return ["UTC", ...Intl.supportedValuesOf("timeZone").filter((z) => z !== "UTC")];
  } catch {
    return ["UTC"];
  }
}

export interface ProjectDraft extends SourceDraft {
  name: string;
  description: string;
  scheduled: boolean;
  scheduleTime: string;
  scheduleTimezone: string;
  scheduleWeekdays: Weekday[];
  /** Notification channels each scheduled report is sent to, with their rules (T026). */
  notifications: NotificationDraft[];
  limitReports: boolean;
  keepReports: number;
}

export function emptyDraft(): ProjectDraft {
  return {
    ...emptySourceDraft(),
    name: "",
    description: "",
    scheduled: false,
    scheduleTime: DEFAULT_SCHEDULE_TIME,
    scheduleTimezone: browserTimezone(),
    scheduleWeekdays: [...ALL_DAYS],
    notifications: [],
    limitReports: false,
    keepReports: DEFAULT_KEEP_REPORTS,
  };
}

/** One chip for a filter accepting any of `values`: an equality for one, PromQL-style alternation for several. */
function anyOfChip(name: string, values: string[]): LabelMatcher {
  return { name, op: values.length === 1 ? "=" : "=~", value: values.join("|") };
}

/** A Cloudflare source's hostname filter as matcher chips (none when it analyses every hostname). */
export function cloudflareFilters(source: NonNullable<ReturnType<typeof cloudflareSource>>): LabelMatcher[] {
  return source.hostnames.length ? [anyOfChip("host", source.hostnames)] : [];
}

/** A Sentry source's project, environment and tag filters as matcher chips. */
export function sentryFilters(source: NonNullable<ReturnType<typeof sentrySource>>): LabelMatcher[] {
  return [
    anyOfChip("project", source.projects),
    ...(source.environment ? [{ name: "environment", op: "=" as const, value: source.environment }] : []),
    ...(source.tags ?? []).map((t) => ({ name: t.key, op: "=" as const, value: t.value })),
  ];
}

export function draftFrom(project: ProjectSummary): ProjectDraft {
  return {
    ...sourceDraftFrom(project),
    name: project.name,
    description: project.description ?? "",
    scheduled: !!project.schedule,
    scheduleTime: project.schedule?.time ?? DEFAULT_SCHEDULE_TIME,
    scheduleTimezone: project.schedule?.timezone ?? browserTimezone(),
    scheduleWeekdays: [...(project.schedule?.weekdays ?? ALL_DAYS)],
    notifications: (project.schedule?.notifications ?? []).map(notificationDraftFrom),
    limitReports: project.keep_reports != null,
    keepReports: project.keep_reports ?? DEFAULT_KEEP_REPORTS,
  };
}

/** A new project prefilled from ``project``, using the same credentials. */
export function cloneDraft(project: ProjectSummary): ProjectDraft {
  return { ...draftFrom(project), name: `${project.name} (copy)`.slice(0, MAX_NAME_CHARS) };
}

export function validateSchedule(draft: ProjectDraft): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.scheduled) return errors;
  if (!LOCAL_TIME.test(draft.scheduleTime)) errors["schedule.time"] = "Expected a time such as 08:00";
  if (!draft.scheduleTimezone.trim()) errors["schedule.timezone"] = "Required";
  if (draft.scheduleWeekdays.length === 0) errors["schedule.weekdays"] = "Choose at least one day";
  return { ...errors, ...validateNotifications(draft.notifications) };
}

export function validateRetention(draft: ProjectDraft): FieldErrors {
  if (!draft.limitReports) return {};
  const n = draft.keepReports;
  if (!Number.isInteger(n) || n < 1 || n > MAX_KEEP_REPORTS) return { keep_reports: `A whole number from 1 to ${MAX_KEEP_REPORTS}` };
  return {};
}

export function validateDraft(draft: ProjectDraft): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = "Required";
  else if (name.length > MAX_NAME_CHARS) errors.name = `At most ${MAX_NAME_CHARS} characters`;
  return { ...errors, ...validateSources(draft), ...validateSchedule(draft), ...validateRetention(draft) };
}

export function prepareProjectSubmission(draft: ProjectDraft) {
  const sources = prepareSourceSubmission(draft);
  const body: ProjectInput = {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    ...sources.fields,
    schedule: draft.scheduled
      ? {
          time: draft.scheduleTime,
          timezone: draft.scheduleTimezone.trim(),
          weekdays: ALL_DAYS.filter((d) => draft.scheduleWeekdays.includes(d)),
          notifications: draft.notifications.map(notificationInput),
        }
      : null,
    keep_reports: draft.limitReports ? draft.keepReports : null,
  };
  return { body, fieldErrors: sources.fieldErrors };
}

export const toInput = (draft: ProjectDraft): ProjectInput => prepareProjectSubmission(draft).body;

/** "Daily at 08:00 (Europe/Berlin)", "Weekdays at …", "Mon, Wed at …". */
export function scheduleSummary(schedule: ReportSchedule): string {
  const days = schedule.weekdays ?? ALL_DAYS;
  const when =
    days.length === ALL_DAYS.length
      ? "Daily"
      : days.length === WORK_DAYS.length && WORK_DAYS.every((d) => days.includes(d))
        ? "Weekdays"
        : WEEKDAYS.filter((w) => days.includes(w.day))
            .map((w) => w.label)
            .join(", ");
  return `${when} at ${schedule.time} (${schedule.timezone})`;
}

export { serverFieldErrors } from "./fieldErrors";

/** Host, port and path prefix of a source URL; "synthetic: <scenario>" for demo sources. */
export function sourceHost(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "synthetic:") return `synthetic: ${parsed.host}`;
    return `${parsed.host}${parsed.pathname === "/" ? "" : parsed.pathname}`;
  } catch {
    return url;
  }
}

export { HEALTH_LABELS, SOURCE_KIND_LABELS, sourceKindLabel, testKey, testVolume } from "./projectSources/display";
export type { HealthKey } from "./projectSources/display";

/** Every source of a report; 2.0 reports only have ``source``. */
export function reportSources(report: ReportSummary): SourceInfo[] {
  if (report.sources?.length) return report.sources;
  return report.source ? [report.source] : [];
}
