<script setup lang="ts">
/** Create (/projects/new), clone (/projects/new?from=:projectId) or edit (/projects/:projectId/edit) a project, test its source, delete it. */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ApiError, type ConnectionTest } from "../../api/client";
import { useCreateProject, useDeleteProject, useProject, useTestConnection, useUpdateProject } from "../../api/queries";
import ConnectionTestResult from "../../components/projects/ConnectionTestResult.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import type { Crumb } from "../../components/shell/crumbs";
import {
  type FieldErrors,
  MAX_KEEP_REPORTS,
  MAX_MATCHERS,
  WEEKDAYS,
  cloneDraft,
  draftFrom,
  emptyDraft,
  keepsStoredSecret,
  matchersInput,
  serverFieldErrors,
  storedAuth,
  testKey,
  testRequestSource,
  timezoneOptions,
  toInput,
  validateDraft,
  validateMatchers,
  validateSource,
} from "../../lib/projects";

const props = defineProps<{ projectId?: string; cloneOf?: string }>();
const TIMEZONES = timezoneOptions();

const router = useRouter();
const editing = computed(() => !!props.projectId);
const cloning = computed(() => !editing.value && !!props.cloneOf);
/** The stored project the form starts from: the one being edited, or the clone's original. */
const sourceId = computed(() => props.projectId ?? props.cloneOf ?? null);
const existing = useProject(sourceId);
const title = computed(() => (editing.value ? "Edit project" : cloning.value ? "Clone project" : "New project"));
const crumbs = computed<Crumb[]>(() => {
  if (!props.projectId) return [{ label: "Projects", to: "/" }, { label: title.value }];
  return [
    { label: "Projects", to: "/" },
    { label: existing.data.value?.name ?? "Project", to: `/projects/${props.projectId}` },
    { label: "Edit" },
  ];
});
const create = useCreateProject();
const update = useUpdateProject();
const remove = useDeleteProject();
const tester = useTestConnection();

const draft = ref(emptyDraft());
const loaded = ref(!sourceId.value);
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
    draft.value = cloning.value ? cloneDraft(project) : draftFrom(project);
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
      project_id: sourceId.value,
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
    const saved = props.projectId
      ? await update.mutateAsync({ id: props.projectId, body })
      : await create.mutateAsync({ body, cloneOf: props.cloneOf });
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
  <AppTopbar :crumbs="crumbs" />
  <main class="page" aria-labelledby="form-title">
    <h1 id="form-title">{{ title }}</h1>

    <div v-if="sourceId && existing.isPending.value" aria-busy="true"><div class="skeleton" /><div class="skeleton" /></div>
    <div v-else-if="sourceId && existing.isError.value" role="alert" class="banner error">{{ existing.error.value?.message }}</div>

    <form v-else class="form stack" novalidate @submit.prevent="save">
      <p v-if="cloning && existing.data.value" class="banner">
        Copied from <RouterLink :to="`/projects/${cloneOf}`">{{ existing.data.value.name }}</RouterLink>, including its stored
        credentials. Reports are not copied.
      </p>
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

      <fieldset>
        <legend>Schedule</legend>
        <label class="check"><input v-model="draft.scheduled" type="checkbox"> Generate a report automatically</label>
        <template v-if="draft.scheduled">
          <p class="muted hint">
            Each run analyses the 24 hours up to the scheduled time. A run missed by more than 6 hours (for example while the app was
            stopped) is skipped.
          </p>
          <div class="schedule">
            <div class="field">
              <label for="schedule-time">Time</label>
              <input
                id="schedule-time"
                v-model="draft.scheduleTime"
                type="time"
                step="60"
                :aria-invalid="!!errors['schedule.time']"
                :aria-describedby="describedBy('schedule.time')"
              >
              <p v-if="errors['schedule.time']" :id="errorId('schedule.time')" class="field-error">{{ errors["schedule.time"] }}</p>
            </div>
            <div class="field">
              <label for="schedule-timezone">Time zone</label>
              <input
                id="schedule-timezone"
                v-model="draft.scheduleTimezone"
                list="schedule-timezones"
                autocomplete="off"
                :aria-invalid="!!errors['schedule.timezone']"
                :aria-describedby="describedBy('schedule.timezone')"
              >
              <datalist id="schedule-timezones"><option v-for="zone in TIMEZONES" :key="zone" :value="zone" /></datalist>
              <p v-if="errors['schedule.timezone']" :id="errorId('schedule.timezone')" class="field-error">{{ errors["schedule.timezone"] }}</p>
            </div>
          </div>
          <div class="field" role="group" aria-labelledby="schedule-days-label" :aria-describedby="describedBy('schedule.weekdays')">
            <span id="schedule-days-label" class="field-label">Days</span>
            <div class="days">
              <label v-for="w in WEEKDAYS" :key="w.day" class="check"><input v-model="draft.scheduleWeekdays" type="checkbox" :value="w.day"> {{ w.label }}</label>
            </div>
            <p v-if="errors['schedule.weekdays']" :id="errorId('schedule.weekdays')" class="field-error">{{ errors["schedule.weekdays"] }}</p>
          </div>
          <p v-if="!draft.url.trim()" class="muted hint">Scheduled runs are skipped until a metrics source is configured.</p>
        </template>
      </fieldset>

      <fieldset>
        <legend>Report retention</legend>
        <label class="check"><input v-model="draft.limitReports" type="checkbox"> Keep only the latest reports</label>
        <div v-if="draft.limitReports" class="field">
          <label for="keep-reports">Reports to keep</label>
          <input
            id="keep-reports"
            v-model.number="draft.keepReports"
            class="keep"
            type="number"
            min="1"
            :max="MAX_KEEP_REPORTS"
            step="1"
            :aria-invalid="!!errors.keep_reports"
            :aria-describedby="describedBy('keep_reports') ?? 'keep-reports-hint'"
          >
          <p id="keep-reports-hint" class="muted hint">
            After each analysis, older analyses and their reports are deleted automatically. Saving a lower number deletes the excess
            right away.
          </p>
          <p v-if="errors.keep_reports" :id="errorId('keep_reports')" class="field-error">{{ errors.keep_reports }}</p>
        </div>
      </fieldset>

      <p v-if="formError" role="alert" class="banner error">{{ formError }}</p>
      <p v-if="saveWarning" class="banner">{{ saveWarning }}</p>
      <div class="row">
        <button type="submit" class="primary" :disabled="saving">{{ saving ? "Saving…" : editing ? "Save changes" : "Create project" }}</button>
        <RouterLink :to="sourceId ? `/projects/${sourceId}` : '/'">Cancel</RouterLink>
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
  </main>
</template>

<style scoped>
.form { max-width: 720px; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label, .field-label { font-weight: 500; }
fieldset { border: 1px solid var(--border); border-radius: var(--radius); padding: calc(var(--space) * 2); background: var(--panel); }
fieldset > * + * { margin-top: var(--space); }
legend { font-weight: 500; color: var(--strong); padding: 0 4px; }
.hint { font-size: 0.85rem; margin: 0; }
.matcher { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1.4fr) auto; gap: var(--space); align-items: start; }
.eq { padding-top: 6px; }
.source-title { margin: 0; font-size: 0.95rem; }
.check { display: flex; gap: 6px; align-items: center; }
.field .check { font-weight: 400; }
.schedule { display: grid; grid-template-columns: 10rem minmax(0, 1fr); gap: var(--space); align-items: start; }
.keep { max-width: 10rem; }
.days { display: flex; flex-wrap: wrap; gap: 4px 16px; }
@media (max-width: 600px) {
  .schedule { grid-template-columns: minmax(0, 1fr); }
}
.danger-zone { max-width: 720px; border-color: var(--crit); }
.danger-zone h2 { margin-top: 0; }
</style>
