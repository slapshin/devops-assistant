<script setup lang="ts">
/** Credentials (/credentials): the secrets of one source kind that project sources authenticate with. */
import { computed, ref, watch } from "vue";
import { ApiError, type Credential, type SourceKind } from "../../api/client";
import { useCreateCredential, useCredentials, useDeleteCredential, useUpdateCredential } from "../../api/queries";
import SecretEditor from "../../components/projects/SecretEditor.vue";
import AppTopbar from "../../components/shell/AppTopbar.vue";
import { CREDENTIAL_MECHANISMS, type FieldErrors, type SecretDraft, emptySecret, mechanismLabel, secretDraftFrom, secretInput, validateSecret } from "../../lib/auth";
import { formatAge, utcTooltip } from "../../lib/format";
import { SOURCE_KIND_LABELS, serverFieldErrors } from "../../lib/projects";

const KINDS = Object.keys(SOURCE_KIND_LABELS) as SourceKind[];
const MAX_NAME_CHARS = 100;

interface CredentialDraft {
  name: string;
  description: string;
  kind: SourceKind;
  auth: SecretDraft;
}

const credentials = useCredentials();
const create = useCreateCredential();
const update = useUpdateCredential();
const remove = useDeleteCredential();

/** null: no form; "new": creating; otherwise the credential being edited. */
const editing = ref<string | null>(null);
const draft = ref<CredentialDraft>(newDraft());
const submitted = ref(false);
const serverErrors = ref<FieldErrors>({});
const formError = ref<string | null>(null);
const deleting = ref<string | null>(null);
const deleteError = ref<{ id: string; message: string } | null>(null);

const items = computed(() => credentials.data.value?.items ?? []);
const current = computed(() => items.value.find((c) => c.credential_id === editing.value) ?? null);
const inUse = computed(() => (current.value?.used_by?.length ?? 0) > 0);
const saving = computed(() => create.isPending.value || update.isPending.value);

function newDraft(kind: SourceKind = "grafana"): CredentialDraft {
  return { name: "", description: "", kind, auth: emptySecret(kind) };
}

// A kind's mechanisms differ: switching the kind of a draft starts its auth over.
watch(
  () => draft.value.kind,
  (kind, previous) => {
    if (kind !== previous && !CREDENTIAL_MECHANISMS[kind].some((m) => m.type === draft.value.auth.type)) {
      draft.value.auth = emptySecret(kind);
    }
  },
);
watch(draft, () => (serverErrors.value = {}), { deep: true });

const clientErrors = computed<FieldErrors>(() => {
  const errors: FieldErrors = {};
  const name = draft.value.name.trim();
  if (!name) errors.name = "Required";
  else if (name.length > MAX_NAME_CHARS) errors.name = `At most ${MAX_NAME_CHARS} characters`;
  // A credential's secret is always required (synthetic sources need none, so they need no credential).
  return { ...errors, ...validateSecret(draft.value.auth, "auth") };
});
const errors = computed<FieldErrors>(() => ({ ...(submitted.value ? clientErrors.value : {}), ...serverErrors.value }));
const errorId = (field: string) => `err-${field.replaceAll(".", "-")}`;
const describedBy = (field: string) => (errors.value[field] ? errorId(field) : undefined);

function applyServerError(error: unknown) {
  if (error instanceof ApiError && error.problem.code === "validation_error") {
    serverErrors.value = serverFieldErrors(error.problem);
    formError.value = "Some fields need attention.";
  } else if (error instanceof ApiError && error.problem.code === "credential_name_taken") {
    serverErrors.value = { name: error.problem.detail ?? "Name already in use" };
  } else {
    formError.value = error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : "Unexpected error";
  }
}

function openNew() {
  editing.value = "new";
  draft.value = newDraft();
  submitted.value = false;
  formError.value = null;
}

function openEdit(credential: Credential) {
  editing.value = credential.credential_id;
  draft.value = {
    name: credential.name,
    description: credential.description ?? "",
    kind: credential.kind,
    auth: secretDraftFrom(credential.kind, credential.auth),
  };
  submitted.value = false;
  formError.value = null;
}

function close() {
  editing.value = null;
  formError.value = null;
  serverErrors.value = {};
}

async function save() {
  submitted.value = true;
  formError.value = null;
  if (Object.keys(clientErrors.value).length) {
    formError.value = "Some fields need attention.";
    return;
  }
  const body = {
    name: draft.value.name.trim(),
    description: draft.value.description.trim() || null,
    kind: draft.value.kind,
    auth: secretInput(draft.value.auth),
  };
  try {
    if (editing.value === "new") await create.mutateAsync(body);
    else if (editing.value) await update.mutateAsync({ id: editing.value, body });
    close();
  } catch (error) {
    applyServerError(error);
  }
}

async function confirmDelete(credential: Credential) {
  deleteError.value = null;
  try {
    await remove.mutateAsync(credential.credential_id);
    deleting.value = null;
    if (editing.value === credential.credential_id) close();
  } catch (error) {
    const message = error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : "Unexpected error";
    deleteError.value = { id: credential.credential_id, message };
  }
}

</script>

<template>
  <AppTopbar :crumbs="[{ label: 'Credentials' }]" />
  <main class="page" aria-labelledby="credentials-title">
    <div class="page-head">
      <h1 id="credentials-title">Credentials</h1>
      <button v-if="editing !== 'new'" type="button" class="primary" @click="openNew">New credential</button>
    </div>
    <p class="muted">
      Tokens and passwords that project sources authenticate with, e.g. one Grafana service account for every project. Rotate a credential here
      once and every source using it connects with the new secret. Secrets are stored encrypted and are never shown again.
    </p>

    <form v-if="editing" class="card form stack" novalidate aria-labelledby="credential-form-title" @submit.prevent="save">
      <h2 id="credential-form-title">{{ editing === "new" ? "New credential" : `Edit ${current?.name ?? "credential"}` }}</h2>
      <div class="field">
        <label for="credential-name">Name</label>
        <input id="credential-name" v-model="draft.name" maxlength="100" :aria-invalid="!!errors.name" :aria-describedby="describedBy('name')">
        <p v-if="errors.name" :id="errorId('name')" class="field-error">{{ errors.name }}</p>
      </div>
      <div class="field">
        <label for="credential-description">Description <span class="muted">(optional)</span></label>
        <input id="credential-description" v-model="draft.description" maxlength="1000">
      </div>
      <div class="field">
        <label for="credential-kind">Source kind</label>
        <select id="credential-kind" v-model="draft.kind" :disabled="inUse" :aria-describedby="inUse ? 'credential-kind-hint' : undefined">
          <option v-for="k in KINDS" :key="k" :value="k">{{ SOURCE_KIND_LABELS[k] }}</option>
        </select>
        <p v-if="inUse" id="credential-kind-hint" class="muted hint">In use, so its kind cannot change.</p>
      </div>
      <SecretEditor :key="draft.kind" v-model="draft.auth" :kind="draft.kind" id-prefix="credential" field-prefix="auth" :errors="errors" />
      <p v-if="formError" role="alert" class="banner error">{{ formError }}</p>
      <div class="row">
        <button type="submit" class="primary" :disabled="saving">{{ saving ? "Saving…" : editing === "new" ? "Create credential" : "Save changes" }}</button>
        <button type="button" @click="close">Cancel</button>
      </div>
    </form>

    <div v-if="credentials.isPending.value" aria-busy="true"><div class="skeleton" style="height: 88px" /></div>
    <div v-else-if="credentials.isError.value" role="alert" class="banner error">
      <strong>Credentials could not be loaded.</strong>
      {{ credentials.error.value?.message }}
      <button type="button" @click="credentials.refetch()">Retry</button>
    </div>
    <div v-else-if="items.length === 0" class="card empty">
      <p>No credentials yet. Create one here, or with “New credential…” next to a source in a project’s form.</p>
    </div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Authentication</th><th scope="col">Used by</th><th scope="col">Updated</th><th scope="col"><span class="sr-only">Actions</span></th></tr>
        </thead>
        <tbody>
          <tr v-for="c in items" :key="c.credential_id">
            <th scope="row">
              {{ c.name }}
              <p v-if="c.description" class="muted hint">{{ c.description }}</p>
              <p v-if="!c.readable" class="field-error">Unreadable: the secret key changed. Enter the secret again.</p>
            </th>
            <td>{{ SOURCE_KIND_LABELS[c.kind] }}</td>
            <td>{{ mechanismLabel(c) }}</td>
            <td>
              <span v-if="!c.used_by?.length" class="muted">Not used</span>
              <ul v-else class="used">
                <li v-for="u in c.used_by" :key="u.project_id"><RouterLink :to="`/projects/${u.project_id}`">{{ u.name }}</RouterLink></li>
              </ul>
            </td>
            <td><time :datetime="c.updated_at" :title="utcTooltip(c.updated_at)">{{ formatAge(c.updated_at) }}</time></td>
            <td class="actions">
              <div class="row">
                <button type="button" :aria-label="`Edit ${c.name}`" @click="openEdit(c)">Edit</button>
                <button v-if="deleting !== c.credential_id" type="button" class="danger" :aria-label="`Delete ${c.name}`" @click="(deleting = c.credential_id), (deleteError = null)">
                  Delete…
                </button>
                <template v-else>
                  <button type="button" class="danger solid" :disabled="remove.isPending.value" @click="confirmDelete(c)">Delete permanently</button>
                  <button type="button" @click="deleting = null">Keep</button>
                </template>
              </div>
              <p v-if="deleteError?.id === c.credential_id" role="alert" class="field-error">{{ deleteError.message }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </main>
</template>

<style scoped>
.form { max-width: 720px; }
.form h2 { margin: 0; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.hint { font-size: 0.85rem; margin: 0; }
.used { list-style: none; margin: 0; padding: 0; }
.actions { white-space: nowrap; }
.actions .field-error { white-space: normal; max-width: 320px; }
</style>
