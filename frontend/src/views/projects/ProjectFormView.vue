<script setup lang="ts">
/** Create (/projects/new) or edit (/projects/:projectId/edit) a project, test its source, delete it. */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ApiError, type ConnectionTest } from "../../api/client";
import { useCreateProject, useDeleteProject, useProject, useTestConnection, useUpdateProject } from "../../api/queries";
import ConnectionTestResult from "../../components/projects/ConnectionTestResult.vue";
import {
  type FieldErrors,
  MAX_MATCHERS,
  draftFrom,
  emptyDraft,
  keepsStoredSecret,
  matchersInput,
  serverFieldErrors,
  storedAuth,
  testKey,
  testRequestSource,
  toInput,
  validateDraft,
  validateMatchers,
  validateSource,
} from "../../lib/projects";

const props = defineProps<{ projectId?: string }>();

const router = useRouter();
const editing = computed(() => !!props.projectId);
const existing = useProject(() => props.projectId ?? null);
const create = useCreateProject();
const update = useUpdateProject();
const remove = useDeleteProject();
const tester = useTestConnection();

const draft = ref(emptyDraft());
const loaded = ref(!props.projectId);
const submitted = ref(false);
const serverErrors = ref<FieldErrors>({});
const formError = ref<string | null>(null);
const lastTest = ref<{ result: ConnectionTest; signature: string } | null>(null);
const confirmingDelete = ref(false);
const deleteName = ref("");

const stored = computed(() => storedAuth(existing.data.value));
const keeps = computed(() => keepsStoredSecret(draft.value, stored.value));

// Fill the form once from the stored project; later refetches must not overwrite edits.
watch(
  () => existing.data.value,
  (project) => {
    if (!project || loaded.value) return;
    draft.value = draftFrom(project);
    loaded.value = true;
  },
  { immediate: true },
);

const clientErrors = computed(() => validateDraft(draft.value, stored.value));
const errors = computed<FieldErrors>(() => ({ ...(submitted.value ? clientErrors.value : {}), ...serverErrors.value }));

// A server error belongs to the value it was about; editing anything clears them.
watch(draft, () => (serverErrors.value = {}), { deep: true });

const sourceSignature = computed(() =>
  JSON.stringify([matchersInput(draft.value), draft.value.url.trim() ? testRequestSource(draft.value) : null]),
);
const testStale = computed(() => !!lastTest.value && lastTest.value.signature !== sourceSignature.value);
const saveWarning = computed(() => {
  if (!draft.value.url.trim()) return "Without a metrics source this project cannot be analysed yet.";
  if (!lastTest.value) return editing.value ? null : "The connection has not been tested.";
  if (testStale.value) return "The connection was not tested with the current values.";
  if (testKey(lastTest.value.result) !== "ok") return "The last connection test did not succeed.";
  return null;
});

const saving = computed(() => create.isPending.value || update.isPending.value);
const active = computed(() => existing.data.value?.active_analysis ?? null);
const canDelete = computed(() => !!existing.data.value && deleteName.value === existing.data.value.name && !active.value);

const errorId = (field: string) => `err-${field.replaceAll(".", "-")}`;
const describedBy = (field: string) => (errors.value[field] ? errorId(field) : undefined);

function addMatcher() {
  if (draft.value.matchers.length < MAX_MATCHERS) draft.value.matchers.push({ name: "", value: "" });
}

function removeMatcher(index: number) {
  draft.value.matchers.splice(index, 1);
}

function problemMessage(error: unknown): string {
  if (error instanceof ApiError) return error.problem.detail ?? error.problem.title;
  return "Unexpected error";
}

function applyServerError(error: unknown) {
  if (error instanceof ApiError && error.problem.code === "validation_error") {
    serverErrors.value = serverFieldErrors(error.problem);
    formError.value = "Some fields need attention.";
  } else if (error instanceof ApiError && error.problem.code === "project_name_taken") {
    serverErrors.value = { name: error.problem.detail ?? "Name already in use" };
  } else {
    formError.value = problemMessage(error);
  }
}

async function runTest() {
  formError.value = null;
  const problems = { ...validateMatchers(draft.value.matchers), ...validateSource(draft.value, stored.value, true) };
  if (Object.keys(problems).length) {
    serverErrors.value = problems;
    return;
  }

  const signature = sourceSignature.value;
  try {
    const result = await tester.mutateAsync({
      project_id: props.projectId ?? null,
      matchers: matchersInput(draft.value),
      source: testRequestSource(draft.value),
    });
    lastTest.value = { result, signature };
  } catch (error) {
    applyServerError(error);
  }
}

async function save() {
  submitted.value = true;
  formError.value = null;
  if (Object.keys(clientErrors.value).length) {
    formError.value = "Some fields need attention.";
    return;
  }

  try {
    const body = toInput(draft.value);
    const saved = props.projectId ? await update.mutateAsync({ id: props.projectId, body }) : await create.mutateAsync(body);
    await router.push(`/projects/${saved.project_id}`);
  } catch (error) {
    applyServerError(error);
  }
}

async function confirmDelete() {
  if (!props.projectId || !canDelete.value) return;
  try {
    await remove.mutateAsync(props.projectId);
    await router.push("/");
  } catch (error) {
    formError.value = problemMessage(error);
  }
}
</script>

<template>
  <section class="stack" aria-labelledby="form-title">
    <p><RouterLink :to="projectId ? `/projects/${projectId}` : '/'">← {{ projectId ? "Project" : "Projects" }}</RouterLink></p>
    <h1 id="form-title">{{ editing ? "Edit project" : "New project" }}</h1>

    <div v-if="editing && existing.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>
    <div v-else-if="editing && existing.isError.value" role="alert" class="banner error">{{ existing.error.value?.message }}</div>

    <form v-else class="form stack" novalidate @submit.prevent="save">
      <div class="field">
        <label for="project-name">Name</label>
        <input id="project-name" v-model="draft.name" maxlength="100" :aria-invalid="!!errors.name" :aria-describedby="describedBy('name')">
        <p v-if="errors.name" :id="errorId('name')" class="field-error">{{ errors.name }}</p>
      </div>
      <div class="field">
        <label for="project-description">Description <span class="muted">(optional)</span></label>
        <textarea id="project-description" v-model="draft.description" rows="2" maxlength="1000" />
      </div>

      <fieldset>
        <legend>Labels</legend>
        <p class="muted hint">Every query of this project is restricted to series with all of these exact label values.</p>
        <div v-for="(m, i) in draft.matchers" :key="i" class="matcher">
          <div class="field">
            <label :for="`matcher-name-${i}`" class="sr-only">Label name {{ i + 1 }}</label>
            <input
              :id="`matcher-name-${i}`"
              v-model="m.name"
              class="mono"
              placeholder="label"
              autocomplete="off"
              :aria-invalid="!!errors[`matchers.${i}.name`]"
              :aria-describedby="describedBy(`matchers.${i}.name`)"
            >
            <p v-if="errors[`matchers.${i}.name`]" :id="errorId(`matchers.${i}.name`)" class="field-error">{{ errors[`matchers.${i}.name`] }}</p>
          </div>
          <span class="eq mono" aria-hidden="true">=</span>
          <div class="field">
            <label :for="`matcher-value-${i}`" class="sr-only">Label value {{ i + 1 }}</label>
            <input
              :id="`matcher-value-${i}`"
              v-model="m.value"
              class="mono"
              placeholder="value"
              autocomplete="off"
              :aria-invalid="!!errors[`matchers.${i}.value`]"
              :aria-describedby="describedBy(`matchers.${i}.value`)"
            >
            <p v-if="errors[`matchers.${i}.value`]" :id="errorId(`matchers.${i}.value`)" class="field-error">{{ errors[`matchers.${i}.value`] }}</p>
          </div>
          <button type="button" :aria-label="`Remove label ${i + 1}`" :disabled="draft.matchers.length === 1" @click="removeMatcher(i)">✕</button>
        </div>
        <p v-if="errors.matchers" class="field-error">{{ errors.matchers }}</p>
        <button type="button" :disabled="draft.matchers.length >= MAX_MATCHERS" @click="addMatcher">Add label</button>
      </fieldset>

      <fieldset>
        <legend>Sources</legend>
        <h2 class="source-title">Prometheus-compatible metrics</h2>
        <div class="field">
          <label for="source-url">URL</label>
          <input
            id="source-url"
            v-model="draft.url"
            class="mono"
            placeholder="http://localhost:8428"
            autocomplete="off"
            :aria-invalid="!!errors['sources.0.url']"
            :aria-describedby="describedBy('sources.0.url') ?? 'source-url-hint'"
          >
          <p id="source-url-hint" class="muted hint">
            Path prefixes are kept. In Docker use <code>host.docker.internal</code> instead of <code>localhost</code>. Leave empty to save
            without a source.
          </p>
          <p v-if="errors['sources.0.url']" :id="errorId('sources.0.url')" class="field-error">{{ errors["sources.0.url"] }}</p>
        </div>
        <label class="check"><input v-model="draft.tlsVerify" type="checkbox"> Verify TLS certificates</label>
        <div class="field">
          <label for="source-auth">Authentication</label>
          <select id="source-auth" v-model="draft.authType">
            <option value="none">None</option>
            <option value="bearer">Bearer token</option>
            <option value="basic">Basic auth</option>
          </select>
        </div>
        <div v-if="draft.authType === 'bearer'" class="field">
          <label for="source-token">Token</label>
          <input
            id="source-token"
            v-model="draft.token"
            type="password"
            autocomplete="new-password"
            :placeholder="keeps ? 'Stored — leave empty to keep' : ''"
            :aria-invalid="!!errors['sources.0.auth.token']"
            :aria-describedby="describedBy('sources.0.auth.token')"
          >
          <p v-if="errors['sources.0.auth.token']" :id="errorId('sources.0.auth.token')" class="field-error">{{ errors["sources.0.auth.token"] }}</p>
        </div>
        <template v-if="draft.authType === 'basic'">
          <div class="field">
            <label for="source-username">Username</label>
            <input
              id="source-username"
              v-model="draft.username"
              autocomplete="off"
              :aria-invalid="!!errors['sources.0.auth.username']"
              :aria-describedby="describedBy('sources.0.auth.username')"
            >
            <p v-if="errors['sources.0.auth.username']" :id="errorId('sources.0.auth.username')" class="field-error">
              {{ errors["sources.0.auth.username"] }}
            </p>
          </div>
          <div class="field">
            <label for="source-password">Password</label>
            <input
              id="source-password"
              v-model="draft.password"
              type="password"
              autocomplete="new-password"
              :placeholder="keeps ? 'Stored — leave empty to keep' : ''"
              :aria-invalid="!!errors['sources.0.auth.password']"
              :aria-describedby="describedBy('sources.0.auth.password')"
            >
            <p v-if="errors['sources.0.auth.password']" :id="errorId('sources.0.auth.password')" class="field-error">
              {{ errors["sources.0.auth.password"] }}
            </p>
          </div>
        </template>
        <p v-if="existing.data.value && !existing.data.value.credentials_readable" class="banner error">
          The stored credentials cannot be decrypted (the secret key changed). Enter them again.
        </p>
        <div class="row">
          <button type="button" :disabled="tester.isPending.value" @click="runTest">
            {{ tester.isPending.value ? "Testing…" : "Test connection" }}
          </button>
        </div>
        <ConnectionTestResult v-if="lastTest" :test="lastTest.result" :stale="testStale" />
      </fieldset>

      <p v-if="formError" role="alert" class="banner error">{{ formError }}</p>
      <p v-if="saveWarning" class="banner">{{ saveWarning }}</p>
      <div class="row">
        <button type="submit" class="primary" :disabled="saving">{{ saving ? "Saving…" : editing ? "Save changes" : "Create project" }}</button>
        <RouterLink :to="projectId ? `/projects/${projectId}` : '/'">Cancel</RouterLink>
      </div>
    </form>

    <section v-if="editing && existing.data.value" class="card danger-zone" aria-labelledby="delete-title">
      <h2 id="delete-title">Delete project</h2>
      <p>
        Deleting removes the project, its stored credentials and
        <strong>{{ existing.data.value.report_count }} saved {{ existing.data.value.report_count === 1 ? "report" : "reports" }}</strong>.
        This cannot be undone.
      </p>
      <p v-if="active" class="banner">An analysis is running for this project. Cancel it or wait for it to finish before deleting.</p>
      <button v-if="!confirmingDelete" type="button" class="danger" :disabled="!!active" @click="confirmingDelete = true">Delete project…</button>
      <div v-else class="stack">
        <label for="delete-confirm">Type <strong>{{ existing.data.value.name }}</strong> to confirm</label>
        <input id="delete-confirm" v-model="deleteName" autocomplete="off">
        <div class="row">
          <button type="button" class="danger solid" :disabled="!canDelete || remove.isPending.value" @click="confirmDelete">
            {{ remove.isPending.value ? "Deleting…" : "Delete permanently" }}
          </button>
          <button type="button" @click="(confirmingDelete = false), (deleteName = '')">Keep project</button>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.form { max-width: 720px; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
textarea { font: inherit; padding: calc(var(--space) * 0.5); border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg); color: var(--text); }
fieldset { border: 1px solid var(--border); border-radius: 8px; padding: calc(var(--space) * 2); background: var(--bg); }
fieldset > * + * { margin-top: var(--space); }
legend { font-weight: 600; padding: 0 4px; }
.hint { font-size: 0.85rem; margin: 0; }
.matcher { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1.4fr) auto; gap: var(--space); align-items: start; }
.eq { padding-top: 6px; }
.source-title { margin: 0; font-size: 0.95rem; }
.check { display: flex; gap: 6px; align-items: center; }
.danger-zone { max-width: 720px; border-color: var(--sev-critical); }
.danger-zone h2 { margin-top: 0; }
</style>
