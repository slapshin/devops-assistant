import type { Problem } from "../api/client";
import type { FieldErrors } from "./auth";
import { normalizeDestinationField } from "./notifications";

/**
 * Field errors from a 422 problem, keyed like validateDraft ("matchers.0.name", "prometheus.url"):
 */
export function serverFieldErrors(problem: Problem): FieldErrors {
  const errors: FieldErrors = {};
  for (const e of problem.errors ?? []) {
    let field = (e.field ?? "").replace(/^body\./, "");
    field = normalizeDestinationField(field);
    errors[field] ??= e.message ?? "Invalid";
  }
  return errors;
}
