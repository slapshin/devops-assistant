import type { Credential, ProjectSummary, Schemas, SourceKind } from "../api/client";

/** Mirrors backend/app/domain/auth.py: sources authenticate only through credentials of their kind, and each kind has its
 * own secret mechanisms. */
export type SecretType = "bearer" | "basic" | "token";
export type FieldErrors = Record<string, string>;

export interface SecretDraft {
  type: SecretType;
  token: string;
  username: string;
  password: string;
  /** Whether the stored credential this draft edits holds a secret of ``type``; null for a new credential. */
  stored: { type: SecretType; secretSet: boolean } | null;
}

interface Mechanism {
  type: SecretType;
  label: string;
}

/** Secret mechanisms of each kind (CREDENTIAL_AUTH_TYPES). */
export const CREDENTIAL_MECHANISMS: Record<SourceKind, Mechanism[]> = {
  prometheus: [
    { type: "bearer", label: "Bearer token" },
    { type: "basic", label: "Basic auth" },
  ],
  cloudflare: [{ type: "token", label: "API token" }],
  sentry: [{ type: "token", label: "Auth token" }],
  wazuh: [{ type: "basic", label: "Username and password" }],
  grafana: [{ type: "token", label: "Service account token" }],
};

/** Label of a kind's token input. */
export const TOKEN_LABELS: Record<SourceKind, string> = {
  prometheus: "Token",
  cloudflare: "API token",
  sentry: "Auth token",
  wazuh: "Token",
  grafana: "Service account token",
};

/** Kinds whose API always needs a credential (except synthetic:// sources); mirrors SECRET_REQUIRED_KINDS. */
export const CREDENTIAL_REQUIRED: readonly SourceKind[] = ["cloudflare", "sentry", "grafana"];

export const emptySecret = (kind: SourceKind, type: SecretType = CREDENTIAL_MECHANISMS[kind][0]!.type): SecretDraft => ({
  type,
  token: "",
  username: "",
  password: "",
  stored: null,
});

/** The editable form of a stored credential's secret; secrets start empty and are kept unless replaced. */
export function secretDraftFrom(kind: SourceKind, auth: Credential["auth"]): SecretDraft {
  return {
    ...emptySecret(kind, auth.type),
    username: auth.type === "basic" ? auth.username : "",
    stored: { type: auth.type, secretSet: auth.type === "basic" ? auth.password_set : auth.token_set },
  };
}

/** True when an empty secret input keeps the stored secret of the chosen mechanism. */
export const keepsSecret = (secret: SecretDraft) => !!secret.stored && secret.stored.type === secret.type && secret.stored.secretSet;

/** Client-side checks of a credential's secret; errors are keyed ``<prefix>.token`` etc. */
export function validateSecret(secret: SecretDraft, prefix: string): FieldErrors {
  const errors: FieldErrors = {};
  const keeps = keepsSecret(secret);
  if (secret.type === "basic") {
    if (!secret.username.trim()) errors[`${prefix}.username`] = "Required";
    if (!secret.password && !keeps) errors[`${prefix}.password`] = "Required";
  } else if (!secret.token && !keeps) {
    errors[`${prefix}.token`] = "Required";
  }
  return errors;
}

/** A credential's auth; an omitted secret keeps the stored one. */
export function secretInput(secret: SecretDraft): Schemas["CredentialInput"]["auth"] {
  if (secret.type === "basic") return { type: "basic", username: secret.username.trim(), ...(secret.password ? { password: secret.password } : {}) };
  return { type: secret.type, ...(secret.token ? { token: secret.token } : {}) };
}

type SourceAuth = ProjectSummary["sources"][number]["auth"];

/** The credential a stored source uses; "" for none. */
export const credentialIdOf = (auth: SourceAuth | undefined) => (auth?.type === "credential" ? auth.credential_id : "");

/** A source's auth in a request: the chosen credential, or none. */
export const sourceAuthInput = (credentialId: string): Schemas["NoAuth"] | Schemas["CredentialRef"] =>
  credentialId ? { type: "credential", credential_id: credentialId } : { type: "none" };

/** Kinds that need a credential get one, except synthetic:// sources. */
export function validateSourceCredential(kind: SourceKind, credentialId: string, synthetic: boolean): FieldErrors {
  if (credentialId || synthetic || !CREDENTIAL_REQUIRED.includes(kind)) return {};
  return { [`${kind}.auth.credential_id`]: "Choose a credential" };
}

/** Short description of a stored source's auth, e.g. "Credential: Grafana production". */
export const authSummary = (auth: SourceAuth) => (auth.type === "credential" ? `Credential: ${auth.name}` : "No authentication");

/** The mechanism of a credential, e.g. "Basic auth (reader)". */
export function mechanismLabel(credential: Credential): string {
  const label = CREDENTIAL_MECHANISMS[credential.kind].find((m) => m.type === credential.auth.type)?.label ?? credential.auth.type;
  return credential.auth.type === "basic" ? `${label} (${credential.auth.username})` : label;
}
