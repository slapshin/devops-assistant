import { computed, ref, type Ref } from "vue";
import { ApiError, type ConnectionTest, type SourceKind } from "../../api/client";
import { useTestConnection } from "../../api/queries";
import type { FieldErrors } from "../auth";
import { sourceKindLabel, testKey } from "./display";
import type { SourceDraft } from "./draft";
import { customRulePreviewBase, prepareConnectionTest, prepareSourceSubmission } from "./editor";

/** Local editing state only; the existing query mutation continues to own the request. */
export function useSourceEditor(draft: Ref<SourceDraft>, editing: Ref<boolean>) {
  const tester = useTestConnection();
  const lastTests = ref<Partial<Record<SourceKind, { result: ConnectionTest; signature: string }>>>({});
  const testing = ref<SourceKind | null>(null);
  const hasSources = computed(() => prepareSourceSubmission(draft.value).fields.sources.length > 0);
  const previewBase = computed(() => customRulePreviewBase(draft.value));
  const isStale = (kind: SourceKind) => {
    const last = lastTests.value[kind];
    return !!last && last.signature !== prepareConnectionTest(draft.value, kind).signature;
  };
  const saveWarning = computed(() => {
    const sources = prepareSourceSubmission(draft.value).fields.sources;
    if (!sources.length) return "Without a data source this project cannot be analysed yet.";
    for (const { kind } of sources) {
      const label = sourceKindLabel(kind);
      const last = lastTests.value[kind];
      if (!last) {
        if (!editing.value) return `The ${label} connection has not been tested.`;
      } else if (isStale(kind)) return `The ${label} connection was not tested with the current values.`;
      else if (testKey(last.result) !== "ok") return `The last ${label} connection test did not succeed.`;
    }
    return null;
  });

  async function test(kind: SourceKind): Promise<{ errors: FieldErrors; message: string | null } | null> {
    const request = prepareConnectionTest(draft.value, kind);
    if (Object.keys(request.errors).length) return { errors: request.errors, message: null };

    testing.value = kind;
    try {
      const result = await tester.mutateAsync(request.body);
      lastTests.value = { ...lastTests.value, [kind]: { result, signature: request.signature } };
      return null;
    } catch (error) {
      if (error instanceof ApiError && error.problem.code === "validation_error") {
        return { errors: request.fieldErrors(error.problem), message: "Some fields need attention." };
      }
      throw error;
    } finally {
      testing.value = null;
    }
  }

  return { test, hasSources, lastTests, testing, isStale, saveWarning, previewBase, pending: tester.isPending };
}
