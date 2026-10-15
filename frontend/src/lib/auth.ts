import type { Credential, ProjectSummary, Schemas, SourceKind } from "../api/client";

/** Mirrors backend/app/domain/auth.py: every source kind has its own authentication mechanisms. */
export type AuthType = "none" | "bearer" | "basic" | "token" | "credential";
export type FieldErrors = Record<string, string>;

/** What the server stores for a source or credential, so an empty secret input keeps it. */
export interface StoredAuth {
  type: AuthType;
  secretSet: boolean;
}

export interface AuthDraft {
  type: AuthType;
  token: string;
  username: string;
  password: string;
  credentialId: string;
  /** The stored mechanism this draft started from; null for a new source. */
  stored: StoredAuth | null;
}

interface Mechanism {
  type: AuthType;
  label: string;
}

/** Inline mechanisms offered per kind. Wazuh's basic auth with an empty username means none, and
 * token kinds send none for synthetic:// sources without a token. */
export const AUTH_MECHANISMS: Record<SourceKind, Mechanism[]> = {
  prometheus: [
    { type: "none", label: "None" },
    { type: "bearer", label: "Bearer token" },
    { type: "basic", label: "Basic auth" },
  ],
  cloudflare: [{ type: "token", label: "API token" }],
  sentry: [{ type: "token", label: "Auth token" }],
  wazuh: [{ type: "basic", label: "Username and password" }],
  grafana: [{ type: "token", label: "Service account token" }],
};

/** Mechanisms a shared credential of each kind can hold (CREDENTIAL_AUTH_TYPES). */
export const CREDENTIAL_MECHANISMS: Record<SourceKind, Mechanism[]> = {
  ...AUTH_MECHANISMS,
  prometheus: AUTH_MECHANISMS.prometheus.filter((m) => m.type !== "none"),
};

/** Label of a kind's token input. */
export const TOKEN_LABELS: Record<SourceKind, string> = {
  prometheus: "Token",
  cloudflare: "API token",
  sentry: "Auth token",
  wazuh: "Token",
  grafana: "Service account token",
};

const DEFAULT_TYPE: Record<SourceKind, AuthType> = {
  prometheus: "none",
  cloudflare: "token",
  sentry: "token",
  wazuh: "basic",
  grafana: "token",
};

export const emptyAuth = (kind: SourceKind, type: AuthType = DEFAULT_TYPE[kind]): AuthDraft => ({
  type,
  token: "",
  username: "",
  password: "",
  credentialId: "",
  stored: null,
});

type SourceAuth = ProjectSummary["sources"][number]["auth"];
type SecretAuth = Credential["auth"];

export function storedAuthOf(auth: SourceAuth | SecretAuth | undefined): StoredAuth | null {
  if (!auth) return null;
  if (auth.type === "bearer" || auth.type === "token") return { type: auth.type, secretSet: auth.token_set };
  if (auth.type === "basic") return { type: "basic", secretSet: auth.password_set };
  return { type: auth.type, secretSet: false };
}

/** The editable form of a stored mechanism; secrets start empty and are kept unless replaced. */
export function authDraftFrom(kind: SourceKind, auth: SourceAuth | SecretAuth | undefined): AuthDraft {
  if (!auth || auth.type === "none") return emptyAuth(kind);
  return {
    ...emptyAuth(kind, auth.type),
    username: auth.type === "basic" ? auth.username : "",
    credentialId: auth.type === "credential" ? auth.credential_id : "",
    stored: storedAuthOf(auth),
  };
}

/** True when an empty secret input keeps the stored secret of the chosen mechanism. */
export const keepsSecret = (auth: AuthDraft) => !!auth.stored && auth.stored.type === auth.type && auth.stored.secretSet;

/** Client-side checks of one source's auth; ``synthetic`` sources need no secret. */
export function validateAuth(kind: SourceKind, auth: AuthDraft, synthetic = false): FieldErrors {
  const errors: FieldErrors = {};
  const field = `${kind}.auth`;
  const keeps = keepsSecret(auth);
  switch (auth.type) {
    case "credential":
      if (!auth.credentialId) errors[`${field}.credential_id`] = "Choose a shared credential";
      break;
    case "bearer":
      if (!auth.token && !keeps) errors[`${field}.token`] = "Required";
      break;
    case "token":
      if (!auth.token && !keeps && !synthetic) errors[`${field}.token`] = "Required";
      break;
    case "basic":
      if (kind === "wazuh") {
        if (auth.username.trim() && !auth.password && !keeps && !synthetic) errors[`${field}.password`] = "Required";
      } else {
        if (!auth.username.trim()) errors[`${field}.username`] = "Required";
        if (!auth.password && !keeps) errors[`${field}.password`] = "Required";
      }
      break;
  }
  return errors;
}

export type AuthInput =
  | Schemas["NoAuth"]
  | Schemas["BearerAuthInput"]
  | Schemas["BasicAuthInput"]
  | Schemas["TokenAuthInput"]
  | Schemas["CredentialRef"];

/** The request body of a source's auth; omitted secrets keep the stored ones. */
export function authInput(kind: SourceKind, auth: AuthDraft): AuthInput {
  const keeps = keepsSecret(auth);
  switch (auth.type) {
    case "credential":
      return { type: "credential", credential_id: auth.credentialId };
    case "bearer":
      return { type: "bearer", ...(auth.token ? { token: auth.token } : {}) };
    case "token":
      if (!auth.token && !keeps) return { type: "none" };
      return { type: "token", ...(auth.token ? { token: auth.token } : {}) };
    case "basic":
      if (kind === "wazuh" && !auth.username.trim()) return { type: "none" };
      return { type: "basic", username: auth.username.trim(), ...(auth.password ? { password: auth.password } : {}) };
    default:
      return { type: "none" };
  }
}

/** A shared credential's auth: always one of its kind's secret mechanisms. */
export function credentialAuthInput(auth: AuthDraft): Schemas["CredentialInput"]["auth"] {
  if (auth.type === "basic") return { type: "basic", username: auth.username.trim(), ...(auth.password ? { password: auth.password } : {}) };
  if (auth.type === "bearer") return { type: "bearer", ...(auth.token ? { token: auth.token } : {}) };
  return { type: "token", ...(auth.token ? { token: auth.token } : {}) };
}

/** Short description of a stored source's auth, e.g. "Shared: Grafana production". */
export function authSummary(kind: SourceKind, auth: SourceAuth): string {
  if (auth.type === "credential") return `Shared credential: ${auth.name}`;
  if (auth.type === "none") return "No authentication";
  const mechanism = AUTH_MECHANISMS[kind].find((m) => m.type === auth.type);
  return mechanism?.label ?? auth.type;
}
