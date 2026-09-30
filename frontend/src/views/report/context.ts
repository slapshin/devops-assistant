import { inject, provide, type ComputedRef, type InjectionKey } from "vue";
import type { AnalysisReport, Evidence, Finding } from "../../api/client";

export interface ReportContext {
  report: ComputedRef<AnalysisReport>;
  findingById: ComputedRef<Map<string, Finding>>;
  evidenceById: ComputedRef<Map<string, Evidence>>;
}

const KEY: InjectionKey<ReportContext> = Symbol("report");

export const provideReport = (ctx: ReportContext) => provide(KEY, ctx);

export function useReportContext(): ReportContext {
  const ctx = inject(KEY);
  if (!ctx) throw new Error("report context missing");
  return ctx;
}
