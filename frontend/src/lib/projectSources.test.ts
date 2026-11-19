import { describe, expect, it } from "vitest";
import { emptyDraft } from "./projects";
import { emptyRule } from "./customRules";
import { customRulePreviewBase, prepareConnectionTest, prepareSourceSubmission, validateSources } from "./projectSources";

describe("source editing", () => {
  it("places save errors using the submitted source order after the draft changes", () => {
    const draft = emptyDraft();
    draft.cloudflare = true;
    draft.zoneId = "a".repeat(32);
    draft.cfApiUrl = "synthetic://healthy";
    const submission = prepareSourceSubmission(draft);

    draft.url = "synthetic://incident";
    const errors = submission.fieldErrors({
      type: "about:blank", title: "Invalid", status: 422, code: "validation_error",
      errors: [{ field: "body.sources.0.zone_id", message: "Unknown zone" }],
    });

    expect(submission.fields.sources.map((source) => source.kind)).toEqual(["cloudflare"]);
    expect(errors).toEqual({ "cloudflare.zone_id": "Unknown zone" });
  });

  it("normalizes source values and preserves their established submission order", () => {
    const draft = emptyDraft();
    Object.assign(draft, {
      url: " synthetic://healthy ",
      cloudflare: true, zoneId: " ABCDEF0123456789ABCDEF0123456789 ",
      hostnames: "WWW.Example.COM., api.example.com www.example.com", cfApiUrl: " synthetic://healthy ",
      sentry: true, sentryOrg: " SHOP ", sentryProjects: "Web, api WEB", sentryEnvironment: " production ",
      wazuh: true, wazuhAgents: "Host-2, Host-1 Host-2", wazuhGroups: "Linux, Linux", wazuhUrl: " synthetic://healthy ",
      grafana: true, grafanaFolders: "Production Alerts, Core\nProduction Alerts", grafanaUrl: " synthetic://healthy ",
    });
    draft.matchers = [{ name: " project ", op: "=", value: "shop" }];
    draft.auth.cloudflare = "cf-credential";

    const { fields } = prepareSourceSubmission(draft);

    expect(fields.matchers).toEqual([{ name: "project", op: "=", value: "shop" }]);
    expect(fields.sources.map((source) => source.kind)).toEqual(["prometheus", "cloudflare", "sentry", "wazuh", "grafana"]);
    expect(fields.sources[1]).toEqual({
      kind: "cloudflare", zone_id: "abcdef0123456789abcdef0123456789",
      hostnames: ["api.example.com", "www.example.com"], api_url: "synthetic://healthy",
      auth: { type: "credential", credential_id: "cf-credential" },
    });
    expect(fields.sources[2]).toMatchObject({ organization: "shop", projects: ["api", "web"], environment: "production" });
    expect(fields.sources[3]).toMatchObject({ agents: ["Host-1", "Host-2"], groups: ["Linux"] });
    expect(fields.sources[4]).toMatchObject({ folders: ["Core", "Production Alerts"] });
    expect(draft.hostnames).toBe("WWW.Example.COM., api.example.com www.example.com");
  });

  it("ignores disabled source validation and serialization while retaining its values and credential", () => {
    const draft = emptyDraft();
    draft.cloudflare = true;
    draft.zoneId = "unfinished";
    draft.auth.cloudflare = "cf-credential";

    expect(validateSources(draft)).toHaveProperty("cloudflare.zone_id");

    draft.cloudflare = false;

    expect(validateSources(draft)).toEqual({});
    expect(prepareSourceSubmission(draft).fields).toEqual({ matchers: [], sources: [] });
    expect(draft.zoneId).toBe("unfinished");
    expect(draft.auth.cloudflare).toBe("cf-credential");

    draft.cloudflare = true;

    expect(prepareSourceSubmission(draft).fields.sources[0]).toMatchObject({
      kind: "cloudflare", zone_id: "unfinished", auth: { type: "credential", credential_id: "cf-credential" },
    });
  });

  it("validates save rules separately from connection tests and scoped previews", () => {
    const draft = emptyDraft();
    draft.url = "synthetic://healthy";
    draft.matchers = [{ name: "project", op: "=", value: "shop" }];
    const beforeRules = prepareConnectionTest(draft, "prometheus");
    draft.customRules = [emptyRule()];

    const test = prepareConnectionTest(draft, "prometheus");
    const preview = customRulePreviewBase(draft);

    expect(Object.keys(validateSources(draft)).length).toBeGreaterThan(0);
    expect(test.errors).toEqual({});
    expect(test.body.source).toHaveProperty("custom_rules", [expect.objectContaining({ slug: "" })]);
    expect(test.signature).toBe(beforeRules.signature);
    expect(preview).toEqual({
      matchers: [{ name: "project", op: "=", value: "shop" }],
      source: { kind: "prometheus", url: "synthetic://healthy", tls_verify: true, auth: { type: "none" }, custom_rules: [] },
    });

    draft.matchers[0]!.value = "";

    expect(customRulePreviewBase(draft)).toBeNull();
    expect(prepareConnectionTest(draft, "prometheus").errors).toHaveProperty("matchers.0.value", "Required");
  });

  it("keeps connection errors attached to the tested kind after source selection changes", () => {
    const draft = emptyDraft();
    draft.grafana = true;
    const request = prepareConnectionTest(draft, "grafana");

    draft.cloudflare = true;
    draft.grafana = false;
    const errors = request.fieldErrors({
      type: "about:blank", title: "Invalid", status: 422, code: "validation_error",
      errors: [{ field: "body.source.auth.credential_id", message: "Wrong credential kind" }],
    });

    expect(errors).toEqual({ "grafana.auth.credential_id": "Wrong credential kind" });
  });

});
