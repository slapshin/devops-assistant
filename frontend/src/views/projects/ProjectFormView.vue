<script setup lang="ts">
/** Create (/projects/new), clone (/projects/new?from=:projectId) or edit (/projects/:projectId/edit) a project, test its sources, delete it. */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ApiError, type ConnectionTest, type SourceKind } from "../../api/client";
import { useCreateProject, useDeleteProject, useProject, useTestConnection, useUpdateProject } from "../../api/queries";
import ConnectionTestResult from "../../components/projects/ConnectionTestResult.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import type { Crumb } from "../../components/shell/crumbs";
import {
  type FieldErrors,
  MAX_KEEP_REPORTS,
  MAX_MATCHERS,
  MAX_SENTRY_PROJECTS,
  MAX_SENTRY_TAGS,
  SENTRY_URL,
  WEEKDAYS,
  cloneDraft,
  cloudflareInput,
  draftFrom,
  emptyDraft,
  keepsStoredSecret,
  matchersInput,
  prometheusInput,
  sentryInput,
  serverFieldErrors,
  sourceInput,
  sourceKindLabel,
  sourceKinds,
  storedAuth,
  storedCloudflareToken,
  storedSentryToken,
  testKey,
  timezoneOptions,
  toInput,
  validateCloudflare,
  validateDraft,
  validateMatchers,
  validateSentry,
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
const lastTests = ref<Partial<Record<SourceKind, { result: ConnectionTest; signature: string }>>>({});
const testing = ref<SourceKind | null>(null);
const confirmingDelete = ref(false);
const deleteName = ref("");

const stored = computed(() => storedAuth(existing.data.value));
const keeps = computed(() => keepsStoredSecret(draft.value, stored.value));
const cfTokenStored = computed(() => storedCloudflareToken(existing.data.value));
const sentryTokenStored = computed(() => storedSentryToken(existing.data.value));

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

const clientErrors = computed(() => validateDraft(draft.value, stored.value, cfTokenStored.value, sentryTokenStored.value));
const errors = computed<FieldErrors>(() => ({ ...(submitted.value ? clientErrors.value : {}), ...serverErrors.value }));

// A server error belongs to the value it was about; editing anything clears them.
watch(draft, () => (serverErrors.value = {}), { deep: true });

const signatures = computed<Record<SourceKind, string>>(() => ({
  prometheus: JSON.stringify([matchersInput(draft.value), draft.value.url.trim() ? prometheusInput(draft.value) : null]),
  cloudflare: JSON.stringify(draft.value.cloudflare ? cloudflareInput(draft.value) : null),
  sentry: JSON.stringify(draft.value.sentry ? sentryInput(draft.value) : null),
}));
const isStale = (kind: SourceKind) => {
  const last = lastTests.value[kind];
  return !!last && last.signature !== signatures.value[kind];
};
const saveWarning = computed(() => {
  const kinds = sourceKinds(draft.value);
  if (!kinds.length) return "Without a data source this project cannot be analysed yet.";
  for (const kind of kinds) {
    const label = sourceKindLabel(kind);
    const last = lastTests.value[kind];
    if (!last) {
      if (!editing.value) return `The ${label} connection has not been tested.`;
    } else if (isStale(kind)) return `The ${label} connection was not tested with the current values.`;
    else if (testKey(last.result) !== "ok") return `The last ${label} connection test did not succeed.`;
  }
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

function applyServerError(error: unknown, tested?: SourceKind) {
  if (error instanceof ApiError && error.problem.code === "validation_error") {
    serverErrors.value = serverFieldErrors(error.problem, sourceKinds(draft.value), tested);
    formError.value = "Some fields need attention.";
  } else if (error instanceof ApiError && error.problem.code === "project_name_taken") {
    serverErrors.value = { name: error.problem.detail ?? "Name already in use" };
  } else {
    formError.value = problemMessage(error);
  }
}

async function runTest(kind: SourceKind) {
  formError.value = null;
  const problems =
    kind === "prometheus"
      ? { ...validateMatchers(draft.value.matchers), ...validateSource(draft.value, stored.value, true) }
      : kind === "cloudflare"
        ? validateCloudflare(draft.value, cfTokenStored.value)
        : validateSentry(draft.value, sentryTokenStored.value);
  if (Object.keys(problems).length) {
    serverErrors.value = problems;
    return;
  }

  const signature = signatures.value[kind];
  testing.value = kind;
  try {
    const result = await tester.mutateAsync({
      project_id: sourceId.value,
      matchers: kind === "prometheus" ? matchersInput(draft.value) : [],
      source: sourceInput(draft.value, kind),
    });
    lastTests.value = { ...lastTests.value, [kind]: { result, signature } };
  } catch (error) {
    applyServerError(error, kind);
  } finally {
    testing.value = null;
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
        <legend>Prometheus-compatible metrics</legend>
        <p class="muted hint">Host, container, HTTP/RPC, proxy and database metrics from VictoriaMetrics or Prometheus.</p>
        <h2 class="source-title">Labels</h2>
        <p class="muted hint">Every query is restricted to series with all of these exact label values. Required with a URL.</p>
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
          <button
            type="button"
            :aria-label="`Remove label ${i + 1}`"
            :disabled="draft.matchers.length === 1 && !!draft.url.trim()"
            @click="removeMatcher(i)"
          >
            ✕
          </button>
        </div>
        <p v-if="errors.matchers" class="field-error">{{ errors.matchers }}</p>
        <button type="button" :disabled="draft.matchers.length >= MAX_MATCHERS" @click="addMatcher">Add label</button>

        <h2 class="source-title">Connection</h2>
        <div class="field">
          <label for="source-url">URL</label>
          <input
            id="source-url"
            v-model="draft.url"
            class="mono"
            placeholder="http://localhost:8428"
            autocomplete="off"
            :aria-invalid="!!errors['prometheus.url']"
            :aria-describedby="describedBy('prometheus.url') ?? 'source-url-hint'"
          >
          <p id="source-url-hint" class="muted hint">
            Path prefixes are kept. In Docker use <code>host.docker.internal</code> instead of <code>localhost</code>. Leave empty for a
            project without Prometheus.
          </p>
          <p v-if="errors['prometheus.url']" :id="errorId('prometheus.url')" class="field-error">{{ errors["prometheus.url"] }}</p>
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
            :aria-invalid="!!errors['prometheus.auth.token']"
            :aria-describedby="describedBy('prometheus.auth.token')"
          >
          <p v-if="errors['prometheus.auth.token']" :id="errorId('prometheus.auth.token')" class="field-error">{{ errors["prometheus.auth.token"] }}</p>
        </div>
        <template v-if="draft.authType === 'basic'">
          <div class="field">
            <label for="source-username">Username</label>
            <input
              id="source-username"
              v-model="draft.username"
              autocomplete="off"
              :aria-invalid="!!errors['prometheus.auth.username']"
              :aria-describedby="describedBy('prometheus.auth.username')"
            >
            <p v-if="errors['prometheus.auth.username']" :id="errorId('prometheus.auth.username')" class="field-error">
              {{ errors["prometheus.auth.username"] }}
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
              :aria-invalid="!!errors['prometheus.auth.password']"
              :aria-describedby="describedBy('prometheus.auth.password')"
            >
            <p v-if="errors['prometheus.auth.password']" :id="errorId('prometheus.auth.password')" class="field-error">
              {{ errors["prometheus.auth.password"] }}
            </p>
          </div>
        </template>
        <div class="row">
          <button type="button" :disabled="tester.isPending.value" @click="runTest('prometheus')">
            {{ testing === "prometheus" ? "Testing…" : "Test Prometheus connection" }}
          </button>
        </div>
        <ConnectionTestResult v-if="lastTests.prometheus" :test="lastTests.prometheus.result" :stale="isStale('prometheus')" />
      </fieldset>

      <fieldset>
        <legend>Cloudflare</legend>
        <label class="check"><input v-model="draft.cloudflare" type="checkbox"> Analyse a Cloudflare zone</label>
        <template v-if="draft.cloudflare">
          <p class="muted hint">
            Edge traffic (requests, 5xx/4xx, origin errors, cache hits, time to first byte) and security events (blocked and challenged
            requests) from the GraphQL Analytics API.
          </p>
          <div class="field">
            <label for="cf-zone">Zone ID</label>
            <input
              id="cf-zone"
              v-model="draft.zoneId"
              class="mono"
              placeholder="32 hex characters"
              autocomplete="off"
              :aria-invalid="!!errors['cloudflare.zone_id']"
              :aria-describedby="describedBy('cloudflare.zone_id') ?? 'cf-zone-hint'"
            >
            <p id="cf-zone-hint" class="muted hint">Cloudflare dashboard → your domain → Overview → API → Zone ID.</p>
            <p v-if="errors['cloudflare.zone_id']" :id="errorId('cloudflare.zone_id')" class="field-error">{{ errors["cloudflare.zone_id"] }}</p>
          </div>
          <div class="field">
            <label for="cf-hostnames">Hostnames <span class="muted">(optional)</span></label>
            <input
              id="cf-hostnames"
              v-model="draft.hostnames"
              class="mono"
              placeholder="shop.example.com, api.example.com"
              autocomplete="off"
              :aria-invalid="!!errors['cloudflare.hostnames']"
              :aria-describedby="describedBy('cloudflare.hostnames') ?? 'cf-hostnames-hint'"
            >
            <p id="cf-hostnames-hint" class="muted hint">Separate with commas or spaces. Empty analyses the whole zone.</p>
            <p v-if="errors['cloudflare.hostnames']" :id="errorId('cloudflare.hostnames')" class="field-error">{{ errors["cloudflare.hostnames"] }}</p>
          </div>
          <div class="field">
            <label for="cf-token">API token</label>
            <input
              id="cf-token"
              v-model="draft.cfToken"
              type="password"
              autocomplete="new-password"
              :placeholder="cfTokenStored ? 'Stored — leave empty to keep' : ''"
              :aria-invalid="!!errors['cloudflare.api_token']"
              :aria-describedby="describedBy('cloudflare.api_token') ?? 'cf-token-hint'"
            >
            <p id="cf-token-hint" class="muted hint">A custom token with the permission <strong>Zone → Analytics → Read</strong> for this zone. Add <strong>Zone → Zone → Read</strong> to show the zone’s domain in findings.</p>
            <p v-if="errors['cloudflare.api_token']" :id="errorId('cloudflare.api_token')" class="field-error">{{ errors["cloudflare.api_token"] }}</p>
          </div>
          <details :open="!!errors['cloudflare.api_url']">
            <summary>Advanced</summary>
            <div class="field">
              <label for="cf-api-url">API URL</label>
              <input
                id="cf-api-url"
                v-model="draft.cfApiUrl"
                class="mono"
                autocomplete="off"
                :aria-invalid="!!errors['cloudflare.api_url']"
                :aria-describedby="describedBy('cloudflare.api_url') ?? 'cf-api-url-hint'"
              >
              <p id="cf-api-url-hint" class="muted hint">GraphQL endpoint; <code>synthetic://incident</code> serves demo data without a token.</p>
              <p v-if="errors['cloudflare.api_url']" :id="errorId('cloudflare.api_url')" class="field-error">{{ errors["cloudflare.api_url"] }}</p>
            </div>
          </details>
          <div class="row">
            <button type="button" :disabled="tester.isPending.value" @click="runTest('cloudflare')">
              {{ testing === "cloudflare" ? "Testing…" : "Test Cloudflare connection" }}
            </button>
          </div>
          <ConnectionTestResult v-if="lastTests.cloudflare" :test="lastTests.cloudflare.result" :stale="isStale('cloudflare')" />
        </template>
      </fieldset>

      <fieldset>
        <legend>Sentry</legend>
        <label class="check"><input v-model="draft.sentry" type="checkbox"> Analyse a Sentry project</label>
        <template v-if="draft.sentry">
          <p class="muted hint">
            Application errors (error events, unhandled errors, affected users) and, when tracing is set up, transactions (throughput,
            failure rate, p95 duration).
          </p>
          <div class="field">
            <label for="sentry-api-url">Sentry URL</label>
            <input
              id="sentry-api-url"
              v-model="draft.sentryApiUrl"
              class="mono"
              list="sentry-urls"
              placeholder="https://sentry.example.com"
              autocomplete="off"
              :aria-invalid="!!errors['sentry.api_url']"
              :aria-describedby="describedBy('sentry.api_url') ?? 'sentry-api-url-hint'"
            >
            <datalist id="sentry-urls"><option :value="SENTRY_URL" /><option value="https://de.sentry.io" /></datalist>
            <p id="sentry-api-url-hint" class="muted hint">
              <code>https://sentry.io</code> (US), <code>https://de.sentry.io</code> (EU), or the address of your self-hosted Sentry, as in
              your browser (path prefixes are kept; in Docker use <code>host.docker.internal</code> instead of <code>localhost</code>).
              <code>synthetic://incident</code> serves demo data without a token.
            </p>
            <p v-if="errors['sentry.api_url']" :id="errorId('sentry.api_url')" class="field-error">{{ errors["sentry.api_url"] }}</p>
          </div>
          <label class="check"><input v-model="draft.sentryTlsVerify" type="checkbox"> Verify TLS certificates</label>
          <p v-if="!draft.sentryTlsVerify" class="muted hint">Only for a self-hosted Sentry with a self-signed or internal certificate.</p>
          <div class="field">
            <label for="sentry-org">Organization</label>
            <input
              id="sentry-org"
              v-model="draft.sentryOrg"
              class="mono"
              placeholder="acme"
              autocomplete="off"
              :aria-invalid="!!errors['sentry.organization']"
              :aria-describedby="describedBy('sentry.organization')"
            >
            <p v-if="errors['sentry.organization']" :id="errorId('sentry.organization')" class="field-error">{{ errors["sentry.organization"] }}</p>
          </div>
          <div class="field">
            <label for="sentry-projects">Sentry projects</label>
            <input
              id="sentry-projects"
              v-model="draft.sentryProjects"
              class="mono"
              placeholder="shop-web, shop-api"
              autocomplete="off"
              :aria-invalid="!!errors['sentry.projects']"
              :aria-describedby="describedBy('sentry.projects') ?? 'sentry-projects-hint'"
            >
            <p id="sentry-projects-hint" class="muted hint">
              Up to {{ MAX_SENTRY_PROJECTS }} project slugs, separated with commas or spaces, as in
              <code>sentry.io/organizations/&lt;organization&gt;/projects/&lt;project&gt;/</code>. Each is analysed separately.
            </p>
            <p v-if="errors['sentry.projects']" :id="errorId('sentry.projects')" class="field-error">{{ errors["sentry.projects"] }}</p>
          </div>
          <div class="field">
            <label for="sentry-environment">Environment <span class="muted">(optional)</span></label>
            <input
              id="sentry-environment"
              v-model="draft.sentryEnvironment"
              class="mono"
              placeholder="production"
              autocomplete="off"
              :aria-invalid="!!errors['sentry.environment']"
              :aria-describedby="describedBy('sentry.environment') ?? 'sentry-environment-hint'"
            >
            <p id="sentry-environment-hint" class="muted hint">Empty analyses all environments together.</p>
            <p v-if="errors['sentry.environment']" :id="errorId('sentry.environment')" class="field-error">{{ errors["sentry.environment"] }}</p>
          </div>
          <div class="field" role="group" aria-labelledby="sentry-tags-label" aria-describedby="sentry-tags-hint">
            <span id="sentry-tags-label" class="field-label">Tag filters <span class="muted">(optional)</span></span>
            <p id="sentry-tags-hint" class="muted hint">
              Only events with all of these exact tag values are analysed, in every project, e.g. <code>server_name</code>,
              <code>release</code> or a custom tag.
            </p>
            <div v-for="(t, i) in draft.sentryTags" :key="i" class="matcher">
              <div class="field">
                <label :for="`sentry-tag-key-${i}`" class="sr-only">Tag key {{ i + 1 }}</label>
                <input
                  :id="`sentry-tag-key-${i}`"
                  v-model="t.key"
                  class="mono"
                  placeholder="tag"
                  autocomplete="off"
                  :aria-invalid="!!errors[`sentry.tags.${i}.key`]"
                  :aria-describedby="describedBy(`sentry.tags.${i}.key`)"
                >
                <p v-if="errors[`sentry.tags.${i}.key`]" :id="errorId(`sentry.tags.${i}.key`)" class="field-error">{{ errors[`sentry.tags.${i}.key`] }}</p>
              </div>
              <span class="eq mono" aria-hidden="true">=</span>
              <div class="field">
                <label :for="`sentry-tag-value-${i}`" class="sr-only">Tag value {{ i + 1 }}</label>
                <input
                  :id="`sentry-tag-value-${i}`"
                  v-model="t.value"
                  class="mono"
                  placeholder="value"
                  autocomplete="off"
                  :aria-invalid="!!errors[`sentry.tags.${i}.value`]"
                  :aria-describedby="describedBy(`sentry.tags.${i}.value`)"
                >
                <p v-if="errors[`sentry.tags.${i}.value`]" :id="errorId(`sentry.tags.${i}.value`)" class="field-error">{{ errors[`sentry.tags.${i}.value`] }}</p>
              </div>
              <button type="button" :aria-label="`Remove tag ${i + 1}`" @click="draft.sentryTags.splice(i, 1)">✕</button>
            </div>
            <p v-if="errors['sentry.tags']" class="field-error">{{ errors["sentry.tags"] }}</p>
            <div>
              <button type="button" :disabled="draft.sentryTags.length >= MAX_SENTRY_TAGS" @click="draft.sentryTags.push({ key: '', value: '' })">Add tag</button>
            </div>
          </div>
          <div class="field">
            <label for="sentry-token">Auth token</label>
            <input
              id="sentry-token"
              v-model="draft.sentryToken"
              type="password"
              autocomplete="new-password"
              :placeholder="sentryTokenStored ? 'Stored — leave empty to keep' : ''"
              :aria-invalid="!!errors['sentry.auth_token']"
              :aria-describedby="describedBy('sentry.auth_token') ?? 'sentry-token-hint'"
            >
            <p id="sentry-token-hint" class="muted hint">
              A personal token or internal integration with the scopes <strong>org:read</strong> and <strong>project:read</strong>.
              Organization tokens (for CI) cannot read events.
            </p>
            <p v-if="errors['sentry.auth_token']" :id="errorId('sentry.auth_token')" class="field-error">{{ errors["sentry.auth_token"] }}</p>
          </div>
          <div class="row">
            <button type="button" :disabled="tester.isPending.value" @click="runTest('sentry')">
              {{ testing === "sentry" ? "Testing…" : "Test Sentry connection" }}
            </button>
          </div>
          <ConnectionTestResult v-if="lastTests.sentry" :test="lastTests.sentry.result" :stale="isStale('sentry')" />
        </template>
      </fieldset>

      <p v-if="existing.data.value && !existing.data.value.credentials_readable" class="banner error">
        The stored credentials cannot be decrypted (the secret key changed). Enter them again.
      </p>

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
          <p v-if="!sourceKinds(draft).length" class="muted hint">Scheduled runs are skipped until a data source is configured.</p>
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
.source-title { margin: calc(var(--space) * 2) 0 0; font-size: 0.95rem; }
fieldset > .hint:first-of-type + .source-title { margin-top: var(--space); }
details summary { cursor: pointer; color: var(--muted); }
details .field { margin-top: var(--space); }
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
