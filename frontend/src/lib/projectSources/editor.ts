import type { ConnectionTestRequest, Problem, SourceKind } from "../../api/client";
import type { FieldErrors } from "../auth";
import { validateCustomRules } from "../customRules";
import { serverFieldErrors } from "../fieldErrors";
import type { SourceDraft } from "./draft";
import { type SourceInput, cloudflareInput, grafanaInput, matchersInput, prometheusInput, sentryInput, wazuhInput } from "./input";
import { validateCloudflare, validateGrafana, validateMatchers, validateSentry, validateSource, validateWazuh } from "./validation";

interface SourceDefinition {
  enabled: (draft: SourceDraft) => boolean;
  input: (draft: SourceDraft) => SourceInput;
  validate: (draft: SourceDraft) => FieldErrors;
  validateConnection?: (draft: SourceDraft) => FieldErrors;
}

/** One fixed catalog owns enabled ordering, conversion and validation for every workflow. */
const SOURCES: Record<SourceKind, SourceDefinition> = {
  prometheus: {
    enabled: (draft: SourceDraft) => !!draft.url.trim(),
    input: prometheusInput,
    validate: (draft: SourceDraft) => ({
      ...validateMatchers(draft.matchers, !!draft.url.trim()),
      ...validateSource(draft),
      ...(draft.url.trim() ? validateCustomRules(draft.customRules) : {}),
    }),
    validateConnection: (draft: SourceDraft) => ({ ...validateMatchers(draft.matchers), ...validateSource(draft, true) }),
  },
  cloudflare: { enabled: (draft: SourceDraft) => draft.cloudflare, input: cloudflareInput, validate: validateCloudflare },
  sentry: { enabled: (draft: SourceDraft) => draft.sentry, input: sentryInput, validate: validateSentry },
  wazuh: { enabled: (draft: SourceDraft) => draft.wazuh, input: wazuhInput, validate: validateWazuh },
  grafana: { enabled: (draft: SourceDraft) => draft.grafana, input: grafanaInput, validate: validateGrafana },
};
const SOURCE_ORDER = Object.keys(SOURCES) as SourceKind[];

export function validateSources(draft: SourceDraft): FieldErrors {
  return Object.assign({}, ...SOURCE_ORDER.map((kind) => SOURCES[kind].validate(draft)));
}

/** Capture the exact source order before an asynchronous save can outlive draft edits. */
export function prepareSourceSubmission(draft: SourceDraft) {
  const kinds = SOURCE_ORDER.filter((kind) => SOURCES[kind].enabled(draft));
  return {
    fields: { matchers: matchersInput(draft), sources: kinds.map((kind) => SOURCES[kind].input(draft)) },
    fieldErrors: (problem: Problem) => sourceFieldErrors(problem, kinds),
  };
}

export function prepareConnectionTest(draft: SourceDraft, kind: SourceKind) {
  const spec = SOURCES[kind];
  const body: ConnectionTestRequest = {
    matchers: kind === "prometheus" ? matchersInput(draft) : [],
    source: spec.input(draft),
  };
  // Custom rules travel in the test request as before, but do not affect connection freshness.
  const connection = kind === "prometheus" ? { ...body.source, custom_rules: [] } : body.source;
  const signature = JSON.stringify([body.matchers, spec.enabled(draft) ? connection : null]);
  return {
    body,
    errors: (spec.validateConnection ?? spec.validate)(draft),
    signature,
    fieldErrors: (problem: Problem) => sourceFieldErrors(problem, [], kind),
  };
}

/** Preview validates only connection and scope; an unfinished rule must not disable its own preview. */
export function customRulePreviewBase(draft: SourceDraft) {
  const test = prepareConnectionTest(draft, "prometheus");
  if (Object.keys(test.errors).length) return null;
  return { matchers: test.body.matchers, source: { ...prometheusInput(draft), custom_rules: [] } };
}

/** Source indices and the singular test source belong to the originating request. */
function sourceFieldErrors(problem: Problem, kinds: SourceKind[], tested?: SourceKind): FieldErrors {
  const errors: FieldErrors = {};
  for (const [field, message] of Object.entries(serverFieldErrors(problem))) {
    let target = field.replace(/^sources\.(\d+)\./, (match, index: string) => kinds[Number(index)] ? `${kinds[Number(index)]}.` : match);
    if (tested) target = target.replace(/^source\./, `${tested}.`);
    errors[target] ??= message;
  }
  return errors;
}
