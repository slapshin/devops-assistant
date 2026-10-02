import { inject, provide, type ComputedRef, type InjectionKey } from "vue";
import type { AnalysisReport, Evidence, Finding } from "../../api/client";

export interface ReportContext {
  report: ComputedRef<AnalysisReport>;
  findingById: ComputedRef<Map<string, Finding>>;
  evidenceById: ComputedRef<Map<string, Evidence>>;
}

const REPORT_CONTEXT_KEY: InjectionKey<ReportContext> = Symbol("report");

export const provideReport = (context: ReportContext) => provide(REPORT_CONTEXT_KEY, context);

export function useReportContext(): ReportContext {
  const context = inject(REPORT_CONTEXT_KEY);
  if (!context) throw new Error("report context missing: useReportContext() must run inside ReportLayout");
  return context;
}
