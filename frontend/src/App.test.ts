import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, type Router } from "vue-router";
import config from "../../fixtures/api/config.json";
import jobCompleted from "../../fixtures/jobs/job_completed.json";
import jobInterrupted from "../../fixtures/jobs/job_interrupted.json";
import jobRunning from "../../fixtures/jobs/job_running.json";
import connectionTest from "../../fixtures/api/connection_test.json";
import cloudflareTest from "../../fixtures/api/connection_test_cloudflare.json";
import sentryTest from "../../fixtures/api/connection_test_sentry.json";
import wazuhTest from "../../fixtures/api/connection_test_wazuh.json";
import grafanaTest from "../../fixtures/api/connection_test_grafana.json";
import customRulePreview from "../../fixtures/api/custom_rule_preview.json";
import credentials from "../../fixtures/api/credentials.json";
import projects from "../../fixtures/api/projects.json";
import aiFailed from "../../fixtures/reports/report_ai_failed.json";
import anomalies from "../../fixtures/reports/report_anomalies.json";
import cloudflareReport from "../../fixtures/reports/report_cloudflare.json";
import sentryReport from "../../fixtures/reports/report_sentry.json";
import wazuhReport from "../../fixtures/reports/report_wazuh.json";
import grafanaReport from "../../fixtures/reports/report_grafana.json";
import customRulesReport from "../../fixtures/reports/report_custom_rules.json";
import healthy from "../../fixtures/reports/report_healthy.json";
import partial from "../../fixtures/reports/report_partial_source_error.json";
import shortHistory from "../../fixtures/reports/report_short_history.json";
import relatedReport from "../../fixtures/reports/report_related.json";
import type { AnalysisJob, Credential, ProjectSummary } from "./api/client";
import App from "./App.vue";
import { makeRouter } from "./router";

type Handler = (init?: RequestInit) => Response | Promise<Response>;

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json", ...headers },
  });
const problem = (status: number, code: string, title = code, detail?: string, headers?: Record<string, string>) =>
  json({ type: "about:blank", title, status, code, detail }, status, headers);

let routes: Record<string, Handler>;
let calls: { method: string; url: string; body?: string }[];
let credentialItems: Credential[];

const VM = credentials.items.find((c) => c.kind === "prometheus") as Credential;
const GRAFANA = credentials.items.find((c) => c.kind === "grafana") as Credential;

/** What the server returns for a created credential (POST /api/credentials). */
function createdCredential(body: { name: string; description?: string | null; kind: Credential["kind"]; auth: { type: string; username?: string } }): Credential {
  const auth = body.auth.type === "basic" ? { type: "basic" as const, username: body.auth.username ?? "", password_set: true } : { type: body.auth.type as "token", token_set: true };
  return { ...GRAFANA, credential_id: `cred-${body.kind}`, name: body.name, description: body.description ?? null, kind: body.kind, auth, used_by: [] };
}

const sourceCredential = (c: Credential) => ({ type: "credential", credential_id: c.credential_id });
const sourceCredentialView = (c: Credential) => ({ ...sourceCredential(c), name: c.name });

beforeEach(() => {
  calls = [];
  routes = {
    "GET /api/config": () => json(config),
    "GET /api/projects": () => json(projects),
    "GET /api/credentials": () => json({ items: credentialItems }),
    "POST /api/credentials": (init) => {
      const created = createdCredential(JSON.parse(String(init?.body)));
      credentialItems = [...credentialItems, created];
      return json(created, 201);
    },
  };
  credentialItems = structuredClone(credentials.items) as Credential[];

  localStorage.clear();

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ method, url, body: init?.body as string | undefined });

      const path = url.split("?")[0];
      const handler = routes[`${method} ${url}`] ?? routes[`${method} ${path}`];

      if (!handler) return problem(404, "analysis_not_found", `no mock for ${method} ${url}`);
      return handler(init);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

async function renderAt(path: string): Promise<Router> {
  const router = makeRouter(createMemoryHistory());
  await router.push(path);
  await router.isReady();

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(App, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient }]],
      stubs: { ChartBox: { props: ["label"], template: '<div role="img" :aria-label="label" />' } },
    },
  });

  return router;
}

function report(fixture: { analysis_id: string }) {
  routes[`GET /api/analyses/${fixture.analysis_id}/report`] = () => json(fixture);
  return `/reports/${fixture.analysis_id}`;
}

type Summary = ProjectSummary;
const base = projects.items[0] as unknown as Summary;
const running = jobRunning as unknown as AnalysisJob;
const PID = base.project_id;
const withId = (id: string, changes: Partial<Summary> = {}): Summary => ({ ...base, project_id: id, name: id, ...changes });
const reachable = { reachable: true, auth_ok: true, matched_series: 12, history_days: 30, families: [], checked_at: "2026-09-30T10:05:00Z" };

describe("projects list", () => {
  it("shows source status, retention, latest report, trend and empty states for every project", async () => {
    const items = [
      withId("ok", { keep_reports: 10 }),
      withId("down", { latest_analysis: null }),
      withId("no-source", { sources: [] }),
    ];
    routes["GET /api/projects"] = () => json({ items });

    await renderAt("/");

    const card = (name: string) => screen.getByRole("article", { name });
    await waitFor(() => expect(card("ok")).toBeInTheDocument());
    expect(within(card("ok")).getByRole("img", { name: "Prometheus: configured" })).toBeInTheDocument();
    expect(within(card("ok")).getByRole("img", { name: "Cloudflare: not configured" })).toBeInTheDocument();
    expect(within(card("ok")).getByRole("img", { name: "Sentry: not configured" })).toBeInTheDocument();
    expect(within(card("no-source")).getByRole("img", { name: "Prometheus: not configured" })).toBeInTheDocument();
    expect(within(card("ok")).getByText("Keeps latest 10 reports")).toBeInTheDocument();
    expect(within(card("down")).getByText("Keeps all reports")).toBeInTheDocument();
    // labels and per-source health stay on the project page
    expect(within(card("ok")).queryByText(/project=/)).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/health"))).toBe(false);

    expect(within(card("ok")).getByRole("link", { name: /Completed/ })).toHaveAttribute("href", `/reports/${base.latest_analysis?.analysis_id}`);
    expect(within(card("ok")).getByText("1 critical")).toBeInTheDocument();
    expect(within(card("ok")).getByRole("img", { name: /Anomaly episodes over 14 days/ })).toBeInTheDocument();
    expect(within(card("down")).getByText("No report yet")).toBeInTheDocument();
    expect(within(card("down")).getByText("No trend yet")).toBeInTheDocument();
    expect(within(card("no-source")).getByRole("button", { name: "Run analysis" })).toBeDisabled();
    expect(within(card("no-source")).getByText(/Add a data source/)).toBeInTheDocument();
  });

  it("runs an analysis from the list and shows its progress inline", async () => {
    let active = false;
    routes["GET /api/projects"] = () => json({ items: [{ ...base, active_analysis: active ? running : null }] });
    routes[`GET /api/projects/${PID}/health`] = () => json([reachable]);
    routes["POST /api/analyses"] = () => {
      active = true;
      return json({ analysis: jobRunning, duplicate_of_active: false }, 202);
    };
    await renderAt("/");

    await fireEvent.click(await screen.findByRole("button", { name: "Run analysis" }));

    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({ project_id: PID });
    expect(await screen.findByRole("link", { name: "Running" })).toHaveAttribute("href", `/analyses/${jobRunning.analysis_id}`);
    expect(screen.getByText(/Collect metrics \(38\/52\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
  });

  it("shows queue-full with the retry time", async () => {
    routes[`GET /api/projects/${PID}/health`] = () => json([reachable]);
    routes["POST /api/analyses"] = () => problem(429, "queue_full", "Analysis queue is full", undefined, { "retry-after": "45" });
    await renderAt("/");

    await fireEvent.click(await screen.findByRole("button", { name: "Run analysis" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 45 seconds");
  });

  it("explains an empty list and a failed load", async () => {
    routes["GET /api/projects"] = () => json({ items: [] });

    await renderAt("/");

    expect(await screen.findByRole("heading", { name: "No projects yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create project" })).toHaveAttribute("href", "/projects/new");
  });

  it("reports projects that cannot be loaded", async () => {
    routes["GET /api/projects"] = () => problem(500, "internal_error", "Internal error");

    await renderAt("/");

    expect(await screen.findByRole("alert")).toHaveTextContent("Projects could not be loaded");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("filters long lists by name or label", async () => {
    const items = Array.from({ length: 11 }, (_, i) => withId(`p${i}`, { sources: [] }));
    routes["GET /api/projects"] = () => json({ items });
    await renderAt("/");

    await fireEvent.update(await screen.findByPlaceholderText("Filter by name or label"), "p10");

    expect(screen.getAllByRole("article")).toHaveLength(1);
  });
});

describe("project form", () => {
  it("sends regex label matchers and needs a positive one", async () => {
    routes["POST /api/projects"] = () => json({ ...base, project_id: "new-id" }, 201);
    routes["GET /api/projects/new-id"] = () => json({ ...base, project_id: "new-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");
    await fireEvent.update(await screen.findByLabelText("Name"), "Shop");
    await fireEvent.update(screen.getByLabelText("URL"), "http://vm:8428");
    await fireEvent.update(screen.getByLabelText("Label operator 1"), "!=");
    await fireEvent.update(screen.getByLabelText("Label value 1"), "blog");
    await fireEvent.update(screen.getByLabelText("Label operator 2"), "!~");
    await fireEvent.update(screen.getByLabelText("Label value 2"), "dev.*");

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(screen.getByText("Add at least one = or =~ label")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await fireEvent.update(screen.getByLabelText("Label operator 1"), "=~");
    await fireEvent.update(screen.getByLabelText("Label value 1"), "shop|shop-\\d+");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/new-id"));
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}").matchers).toEqual([
      { name: "project", op: "=~", value: "shop|shop-\\d+" },
      { name: "env", op: "!~", value: "dev.*" },
    ]);
  });

  it("validates like the server before creating", async () => {
    routes["POST /api/projects"] = () => json({ ...base, project_id: "new-id" }, 201);
    routes["GET /api/projects/new-id"] = () => json({ ...base, project_id: "new-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.click(await screen.findByRole("button", { name: "Create project" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Some fields need attention");
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    // labels are required only once a Prometheus URL is set
    expect(screen.getByLabelText("Label value 1")).not.toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(screen.getByLabelText("URL"), "http://vm:8428");

    expect(screen.getByLabelText("Label value 1")).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await fireEvent.update(screen.getByLabelText("Name"), "Shop");
    await fireEvent.update(screen.getByLabelText("Label name 2"), "__name__");
    await fireEvent.update(screen.getByLabelText("Label value 1"), "shop");
    await fireEvent.update(screen.getByLabelText("Label value 2"), "prod");

    expect(await screen.findByText("Labels starting with __ are reserved")).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("Label name 2"), "env");
    await fireEvent.update(screen.getByLabelText("URL"), "http://user:pw@vm:8428");

    expect(await screen.findByText(/Must not contain credentials/)).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("URL"), "http://vm:8428/prom");
    await waitFor(() => expect(screen.getByRole("option", { name: VM.name })).toBeInTheDocument());
    await fireEvent.update(screen.getByLabelText("Credential"), VM.credential_id);

    expect(screen.getByText("The Prometheus connection has not been tested.")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/new-id"));
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({
      name: "Shop",
      description: null,
      matchers: [
        { name: "project", op: "=", value: "shop" },
        { name: "env", op: "=", value: "prod" },
      ],
      sources: [{ kind: "prometheus", url: "http://vm:8428/prom", tls_verify: true, auth: sourceCredential(VM) }],
      schedule: null,
      keep_reports: null,
    });
  });

  it("limits how many reports a project keeps", async () => {
    routes[`GET /api/projects/${PID}`] = () => json({ ...base, keep_reports: null });
    routes[`PUT /api/projects/${PID}`] = () => json(base);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt(`/projects/${PID}/edit`);

    await fireEvent.click(await screen.findByLabelText("Keep only the latest reports"));

    expect(screen.getByLabelText("Reports to keep")).toHaveValue(10);

    await fireEvent.update(screen.getByLabelText("Reports to keep"), "0");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(screen.getByText("A whole number from 1 to 1000")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    await fireEvent.update(screen.getByLabelText("Reports to keep"), "5");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe(`/projects/${PID}`));
    expect(JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}").keep_reports).toBe(5);
  });

  it("configures a report schedule and shows server time zone errors", async () => {
    routes[`GET /api/projects/${PID}`] = () => json({ ...base, schedule: null, next_scheduled_run: null });
    let attempt = 0;
    routes[`PUT /api/projects/${PID}`] = () =>
      ++attempt === 1
        ? json({ type: "about:blank", title: "Request validation failed", status: 422, code: "validation_error", errors: [{ field: "body.schedule.timezone", message: "unknown time zone" }] }, 422)
        : json(base);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt(`/projects/${PID}/edit`);

    await fireEvent.click(await screen.findByLabelText("Generate a report automatically"));

    expect(screen.getByLabelText("Time")).toHaveValue("08:00");

    for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) await fireEvent.click(screen.getByLabelText(day));
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(screen.getByText("Choose at least one day")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    await fireEvent.click(screen.getByLabelText("Fri"));
    await fireEvent.click(screen.getByLabelText("Mon"));
    await fireEvent.update(screen.getByLabelText("Time"), "07:30");
    await fireEvent.update(screen.getByLabelText("Time zone"), "Mars/Olympus");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("unknown time zone")).toBeInTheDocument();
    expect(screen.getByLabelText("Time zone")).toHaveAttribute("aria-invalid", "true");
    expect(JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}").schedule).toEqual({
      time: "07:30",
      timezone: "Mars/Olympus",
      weekdays: ["mon", "fri"],
    });

    await fireEvent.update(screen.getByLabelText("Time zone"), "Europe/Berlin");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe(`/projects/${PID}`));
    expect(JSON.parse(calls.filter((c) => c.method === "PUT")[1]?.body ?? "{}").schedule.timezone).toBe("Europe/Berlin");
  });

  it("keeps the source's credential and shows server errors", async () => {
    routes[`GET /api/projects/${PID}`] = () => json(base);
    let attempt = 0;
    routes[`PUT /api/projects/${PID}`] = () =>
      ++attempt === 1
        ? json({ type: "about:blank", title: "Request validation failed", status: 422, code: "validation_error", errors: [{ field: "body.matchers.0.name", message: "bad label" }] }, 422)
        : json(base);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}/edit`);

    await waitFor(() => expect(screen.getByLabelText("Credential")).toHaveValue(VM.credential_id));
    expect(screen.getByLabelText("Name")).toHaveValue(base.name);

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("bad label")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}").sources[0].auth).toEqual(sourceCredential(VM));

    await fireEvent.update(screen.getByLabelText("Credential"), "");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.method === "PUT")[1]?.body ?? "{}").sources[0].auth).toEqual({ type: "none" });
  });

  it("clones a project with all settings and its credential", async () => {
    const original = { ...base, schedule: { time: "07:30", timezone: "Europe/Berlin", weekdays: ["mon"] }, keep_reports: 5 } as Summary;
    routes[`GET /api/projects/${PID}`] = () => json(original);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    routes["POST /api/projects"] = () => json({ ...base, project_id: "clone-id" }, 201);
    routes["GET /api/projects/clone-id"] = () => json({ ...base, project_id: "clone-id" });
    routes["POST /api/projects/test-connection"] = () => json(connectionTest);

    const router = await renderAt(`/projects/${PID}`);
    await fireEvent.click(await screen.findByRole("link", { name: "Clone" }));

    expect(await screen.findByRole("heading", { name: "Clone project" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Name")).toHaveValue(`${base.name} (copy)`);
    await waitFor(() => expect(screen.getByLabelText("Credential")).toHaveValue(VM.credential_id));
    expect(screen.queryByRole("heading", { name: "Delete project" })).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Test Prometheus connection" }));

    await waitFor(() => expect(calls.some((c) => c.url === "/api/projects/test-connection")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/projects/test-connection")?.body ?? "{}").source.auth).toEqual(sourceCredential(VM));

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/clone-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.matchers).toEqual(base.matchers);
    expect(sent.sources[0].auth).toEqual(sourceCredential(VM));
    expect(sent.schedule).toEqual(original.schedule);
    expect(sent.keep_reports).toBe(5);
  });

  it("tests the connection with the draft and marks the result stale after edits", async () => {
    routes["POST /api/projects/test-connection"] = () => json(connectionTest);
    await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Label value 1"), "shop");
    await fireEvent.update(screen.getByLabelText("Label value 2"), "production");
    await fireEvent.click(screen.getByRole("button", { name: "Test Prometheus connection" }));

    expect(await screen.findByText("Required to test the connection")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("test-connection"))).toBe(false);

    await fireEvent.update(screen.getByLabelText("URL"), "http://vm:8428");
    await fireEvent.click(screen.getByRole("button", { name: "Test Prometheus connection" }));

    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 1832 matching series · 30 days of history");
    expect(within(result).getByRole("row", { name: /Latency.*Unsupported.*histogram/ })).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({
      matchers: [
        { name: "project", op: "=", value: "shop" },
        { name: "env", op: "=", value: "production" },
      ],
      source: { kind: "prometheus", url: "http://vm:8428", tls_verify: true, auth: { type: "none" } },
    });
    expect(screen.queryByText(/not tested/)).not.toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("URL"), "http://other:8428");

    expect(screen.getByText(/The form changed after this test/)).toBeInTheDocument();
    expect(screen.getByText("The Prometheus connection was not tested with the current values.")).toBeInTheDocument();
  });

  it("creates a Cloudflare-only project without labels and tests it", async () => {
    routes["POST /api/projects/test-connection"] = () => json(cloudflareTest);
    routes["POST /api/projects"] = () => json({ ...base, project_id: "cf-id" }, 201);
    routes["GET /api/projects/cf-id"] = () => json({ ...base, project_id: "cf-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop edge");

    expect(screen.queryByLabelText("Zone ID")).not.toBeInTheDocument();

    await fireEvent.click(screen.getByLabelText("Analyse a Cloudflare zone"));

    await fireEvent.update(screen.getByLabelText("Zone ID"), "not-a-zone");
    await fireEvent.update(screen.getByLabelText(/Hostnames/), "Shop.Example.com, bad_host");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(screen.getByLabelText("Zone ID")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Invalid hostnames: bad_host")).toBeInTheDocument();
    const cfGroup = screen.getByRole("group", { name: "Cloudflare" });
    expect(within(cfGroup).getByLabelText("Credential")).toHaveAttribute("aria-invalid", "true");
    expect(within(cfGroup).getByText("Choose a credential")).toBeInTheDocument();
    expect(screen.getByLabelText("Label value 1")).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("The Cloudflare connection has not been tested.")).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("Zone ID"), "0123456789ABCDEF0123456789ABCDEF");
    await fireEvent.update(screen.getByLabelText(/Hostnames/), "Shop.Example.com api.example.com");
    await fireEvent.click(within(cfGroup).getByRole("button", { name: "New credential…" }));
    await fireEvent.click(within(cfGroup).getByRole("button", { name: "Create credential" }));

    expect(within(cfGroup).getByLabelText("Credential name")).toHaveAttribute("aria-invalid", "true");
    expect(within(cfGroup).getByLabelText("API token")).toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(within(cfGroup).getByLabelText("Credential name"), "Cloudflare shop");
    await fireEvent.update(within(cfGroup).getByLabelText("API token"), "cf-token");
    await fireEvent.click(within(cfGroup).getByRole("button", { name: "Create credential" }));

    await waitFor(() => expect(within(cfGroup).getByLabelText("Credential")).toHaveValue("cred-cloudflare"));
    expect(JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/credentials")?.body ?? "{}")).toEqual({
      name: "Cloudflare shop",
      kind: "cloudflare",
      auth: { type: "token", token: "cf-token" },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Test Cloudflare connection" }));

    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 3,456,789 requests in 24 h · 30 days of history");
    expect(within(result).getByRole("row", { name: /Security \(WAF\).*Supported/ })).toBeInTheDocument();

    const source = { kind: "cloudflare", zone_id: "0123456789abcdef0123456789abcdef", hostnames: ["api.example.com", "shop.example.com"], api_url: "https://api.cloudflare.com/client/v4/graphql", auth: { type: "credential", credential_id: "cred-cloudflare" } };
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({ matchers: [], source });

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/cf-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.matchers).toEqual([]);
    expect(sent.sources).toEqual([source]);
  });

  it("keeps a Cloudflare source's credential and maps server errors to the right source", async () => {
    const cf = createdCredential({ name: "Cloudflare shop", kind: "cloudflare", auth: { type: "token" } });
    credentialItems = [...credentialItems, cf];
    const cloudflare = { kind: "cloudflare", zone_id: "0123456789abcdef0123456789abcdef", hostnames: ["shop.example.com"], api_url: "https://api.cloudflare.com/client/v4/graphql", auth: sourceCredentialView(cf) };
    const both = { ...base, sources: [...base.sources, cloudflare] } as Summary;
    routes[`GET /api/projects/${PID}`] = () => json(both);
    routes[`PUT /api/projects/${PID}`] = () =>
      json({ type: "about:blank", title: "Request validation failed", status: 422, code: "validation_error", errors: [{ field: "body.sources.1.zone_id", message: "zone rejected" }] }, 422);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}/edit`);

    const cfGroup = await screen.findByRole("group", { name: "Cloudflare" });
    await waitFor(() => expect(within(cfGroup).getByLabelText("Credential")).toHaveValue(cf.credential_id));
    expect(screen.getByLabelText(/Hostnames/)).toHaveValue("shop.example.com");

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("zone rejected")).toBeInTheDocument();
    expect(screen.getByLabelText("Zone ID")).toHaveAttribute("aria-invalid", "true");

    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources.map((x: { kind: string }) => x.kind)).toEqual(["prometheus", "cloudflare"]);
    expect(put.sources[1].auth).toEqual(sourceCredential(cf));
  });

  it("creates a Sentry project, validates slugs and tests it", async () => {
    const sentryCredential = createdCredential({ name: "Sentry acme", kind: "sentry", auth: { type: "token" } });
    credentialItems = [...credentialItems, sentryCredential];
    routes["POST /api/projects/test-connection"] = () => json(sentryTest);
    routes["POST /api/projects"] = () => json({ ...base, project_id: "sentry-id" }, 201);
    routes["GET /api/projects/sentry-id"] = () => json({ ...base, project_id: "sentry-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop app");

    expect(screen.queryByLabelText("Organization")).not.toBeInTheDocument();

    await fireEvent.click(screen.getByLabelText("Analyse a Sentry project"));

    await fireEvent.update(screen.getByLabelText("Organization"), "acme corp");
    await fireEvent.update(screen.getByLabelText("Sentry projects"), "shop-web, bad slug!");
    await fireEvent.update(screen.getByLabelText(/Environment/), "prod/eu");
    await fireEvent.click(screen.getByRole("button", { name: "Add tag" }));
    await fireEvent.update(screen.getByLabelText("Tag key 1"), "bad key");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(screen.getByLabelText("Organization")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Invalid project slugs: slug!")).toBeInTheDocument();
    expect(screen.getByLabelText("Tag key 1")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Tag value 1")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(/Environment/)).toHaveAttribute("aria-invalid", "true");
    const sentryGroup = screen.getByRole("group", { name: "Sentry" });
    expect(within(sentryGroup).getByLabelText("Credential")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("The Sentry connection has not been tested.")).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("Organization"), "Acme");
    await fireEvent.update(screen.getByLabelText("Sentry projects"), "Shop-Web shop-api");
    await fireEvent.update(screen.getByLabelText("Tag key 1"), "team");
    await fireEvent.update(screen.getByLabelText("Tag value 1"), "shop");
    await fireEvent.click(screen.getByRole("button", { name: "Add tag" }));
    await fireEvent.update(screen.getByLabelText(/Environment/), "production");
    await fireEvent.update(within(sentryGroup).getByLabelText("Credential"), sentryCredential.credential_id);
    await fireEvent.click(screen.getByRole("button", { name: "Test Sentry connection" }));

    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 1,284,310 events in 24 h · 30 days of history");
    expect(within(result).getByRole("row", { name: /Transactions \(Sentry\).*Supported/ })).toBeInTheDocument();

    const source = {
      kind: "sentry",
      organization: "acme",
      projects: ["shop-api", "shop-web"],
      tags: [{ key: "team", value: "shop" }],
      environment: "production",
      api_url: "https://sentry.io",
      tls_verify: true,
      auth: sourceCredential(sentryCredential),
    };
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({ matchers: [], source });

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/sentry-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.sources).toEqual([source]);
  });

  it("edits a self-hosted Sentry and keeps its credential", async () => {
    const url = "https://sentry.internal:9000/sentry";
    const sentryCredential = createdCredential({ name: "Sentry internal", kind: "sentry", auth: { type: "token" } });
    credentialItems = [...credentialItems, sentryCredential];
    const sentry = { kind: "sentry", organization: "acme", projects: ["shop-web"], tags: [{ key: "team", value: "shop" }], environment: null, api_url: url, tls_verify: false, auth: sourceCredentialView(sentryCredential) };

    routes[`GET /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, sentry] } as Summary);
    routes[`PUT /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, sentry] });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}/edit`);

    const sentryGroup = await screen.findByRole("group", { name: "Sentry" });
    await waitFor(() => expect(within(sentryGroup).getByLabelText("Credential")).toHaveValue(sentryCredential.credential_id));
    expect(screen.getByLabelText("Sentry projects")).toHaveValue("shop-web");
    expect(screen.getByLabelText("Tag value 1")).toHaveValue("shop");
    expect(screen.getByLabelText("Sentry URL")).toBeVisible();
    expect(screen.getByLabelText("Sentry URL")).toHaveValue(url);

    expect(within(sentryGroup).getByLabelText("Verify TLS certificates")).not.toBeChecked();
    expect(within(sentryGroup).getByText(/self-signed or internal certificate/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));

    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources.map((x: { kind: string }) => x.kind)).toEqual(["prometheus", "sentry"]);
    expect(put.sources[1]).toEqual({ kind: "sentry", organization: "acme", projects: ["shop-web"], tags: [{ key: "team", value: "shop" }], environment: null, api_url: url, tls_verify: false, auth: sourceCredential(sentryCredential) });
  });

  it("creates a Wazuh source selected by labels and tests it", async () => {
    routes["POST /api/projects/test-connection"] = () => json(wazuhTest);
    routes["POST /api/projects"] = () => json({ ...base, project_id: "wazuh-id" }, 201);
    routes["GET /api/projects/wazuh-id"] = () => json({ ...base, project_id: "wazuh-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop hosts");
    await fireEvent.click(screen.getByLabelText("Analyse Wazuh agents"));
    const group = screen.getByRole("group", { name: "Wazuh" });

    await fireEvent.update(within(group).getByLabelText(/^Agents/), "web 1!");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(within(group).getByLabelText("Indexer URL")).toHaveAttribute("aria-invalid", "true");
    expect(within(group).getByText("Invalid agent names: 1!")).toBeInTheDocument();
    expect(within(group).getByLabelText("Credential")).toHaveValue("");

    await fireEvent.click(within(group).getByRole("button", { name: "New credential…" }));
    await fireEvent.update(within(group).getByLabelText("Credential name"), "Wazuh reader");
    await fireEvent.update(within(group).getByLabelText("Username"), "reader");
    await fireEvent.click(within(group).getByRole("button", { name: "Create credential" }));

    expect(within(group).getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(within(group).getByLabelText("Password"), "s3cret");
    await fireEvent.click(within(group).getByRole("button", { name: "Create credential" }));

    await waitFor(() => expect(within(group).getByLabelText("Credential")).toHaveValue("cred-wazuh"));

    await fireEvent.update(within(group).getByLabelText(/^Agents/), "");
    await fireEvent.update(within(group).getByLabelText("Indexer URL"), "https://wazuh-indexer:9200/");
    await fireEvent.click(within(group).getByRole("button", { name: "Add label" }));
    await fireEvent.update(within(group).getByLabelText("Label key 1"), "project");
    await fireEvent.update(within(group).getByLabelText("Label value 1"), "shop");
    await fireEvent.click(within(group).getByLabelText("Verify TLS certificates"));
    await fireEvent.click(screen.getByRole("button", { name: "Test Wazuh connection" }));

    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 8,412 alerts in 24 h · 28 days of history");
    expect(within(result).getByRole("row", { name: /File integrity \(Wazuh\).*Supported/ })).toBeInTheDocument();

    const source = {
      kind: "wazuh",
      api_url: "https://wazuh-indexer:9200/",
      index_pattern: "wazuh-alerts-4.x-*",
      agents: [],
      groups: [],
      monitoring_index_pattern: "wazuh-monitoring-*",
      labels: [{ key: "project", value: "shop" }],
      tls_verify: false,
      auth: { type: "credential", credential_id: "cred-wazuh" },
    };
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({ matchers: [], source });

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/wazuh-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.sources).toEqual([source]);
  });

  it("selects Wazuh agents by group and validates group names", async () => {
    routes["POST /api/projects"] = () => json({ ...base, project_id: "wazuh-id" }, 201);
    routes["GET /api/projects/wazuh-id"] = () => json({ ...base, project_id: "wazuh-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop hosts");
    await fireEvent.click(screen.getByLabelText("Analyse Wazuh agents"));
    const group = screen.getByRole("group", { name: "Wazuh" });
    await fireEvent.update(within(group).getByLabelText("Indexer URL"), "synthetic://incident");

    expect(within(group).queryByLabelText("Monitoring index pattern")).not.toBeInTheDocument();

    await fireEvent.update(within(group).getByLabelText(/^Agent groups/), "shop-web, web/servers");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(within(group).getByText("Invalid group names: web/servers")).toBeInTheDocument();
    expect(within(group).getByLabelText(/^Agents/)).not.toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(within(group).getByLabelText(/^Agent groups/), "shop-web shop-db");

    expect(within(group).getByLabelText("Monitoring index pattern")).toHaveValue("wazuh-monitoring-*");

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/wazuh-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.sources[0]).toMatchObject({ kind: "wazuh", agents: [], groups: ["shop-db", "shop-web"], labels: [] });
  });

  it("edits a Wazuh source and keeps its credential", async () => {
    const reader = createdCredential({ name: "Wazuh reader", kind: "wazuh", auth: { type: "basic", username: "reader" } });
    credentialItems = [...credentialItems, reader];
    const wazuh = {
      kind: "wazuh",
      api_url: "https://wazuh-indexer:9200",
      index_pattern: "wazuh-alerts-4.x-*",
      agents: ["shop-db-1", "shop-web-1"],
      groups: ["ops"],
      monitoring_index_pattern: "wazuh-monitoring-*",
      labels: [],
      tls_verify: true,
      auth: sourceCredentialView(reader),
    };

    routes[`GET /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, wazuh] } as Summary);
    routes[`PUT /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, wazuh] });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}/edit`);

    const group = await screen.findByRole("group", { name: "Wazuh" });
    await waitFor(() => expect(within(group).getByLabelText("Credential")).toHaveValue(reader.credential_id));
    expect(within(group).getByLabelText(/^Agents/)).toHaveValue("shop-db-1, shop-web-1");
    expect(within(group).getByLabelText(/^Agent groups/)).toHaveValue("ops");

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));

    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources.map((x: { kind: string }) => x.kind)).toEqual(["prometheus", "wazuh"]);
    expect(put.sources[1]).toEqual({ ...wazuh, auth: sourceCredential(reader) });
  });

  it("creates a Grafana alerts source and tests it", async () => {
    routes["POST /api/projects/test-connection"] = () => json(grafanaTest);
    routes["POST /api/projects"] = () => json({ ...base, project_id: "grafana-id" }, 201);
    routes["GET /api/projects/grafana-id"] = () => json({ ...base, project_id: "grafana-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop alerts");
    await fireEvent.click(screen.getByLabelText("Analyse Grafana alert rules"));
    const group = screen.getByRole("group", { name: "Grafana alerts" });

    await fireEvent.update(within(group).getByLabelText(/^Rule UIDs/), "ok-1 bad/uid");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(within(group).getByLabelText("Grafana URL")).toHaveAttribute("aria-invalid", "true");
    expect(within(group).getByText("Invalid rule UIDs: bad/uid")).toBeInTheDocument();
    expect(within(group).getByLabelText("Credential")).toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(within(group).getByLabelText(/^Rule UIDs/), "");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(within(group).getByText("Select alert rules by folder, UID or rule label")).toBeInTheDocument();

    await fireEvent.update(within(group).getByLabelText("Grafana URL"), "https://grafana.example.com");
    await fireEvent.update(within(group).getByLabelText(/^Folders/), "Shop alerts, Infrastructure, Shop alerts");
    await fireEvent.click(within(group).getByRole("button", { name: "Add instance label" }));
    await fireEvent.update(within(group).getByLabelText("Instance labels name 1"), "project");
    await fireEvent.update(within(group).getByLabelText("Instance labels value 1"), "shop");
    await fireEvent.update(within(group).getByLabelText("Credential"), GRAFANA.credential_id);
    await fireEvent.click(screen.getByRole("button", { name: "Test Grafana connection" }));

    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 6 matching alert instances");
    expect(within(result).getByRole("row", { name: /Alerting \(Grafana\).*Supported/ })).toBeInTheDocument();

    const source = {
      kind: "grafana",
      api_url: "https://grafana.example.com",
      org_id: 1,
      folders: ["Infrastructure", "Shop alerts"],
      rule_uids: [],
      rule_labels: [],
      instance_labels: [{ name: "project", op: "=", value: "shop" }],
      tls_verify: true,
      auth: sourceCredential(GRAFANA),
    };
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({ matchers: [], source });

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/grafana-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.sources).toEqual([source]);
  });

  it("edits a Grafana source and keeps its credential", async () => {
    const grafana = {
      kind: "grafana",
      api_url: "https://grafana.example.com",
      org_id: 2,
      folders: ["Infrastructure"],
      rule_uids: ["dfxnb7qy1wlxcc"],
      rule_labels: [{ name: "team", op: "=", value: "web" }],
      instance_labels: [{ name: "project", op: "=", value: "shop" }],
      tls_verify: true,
      auth: sourceCredentialView(GRAFANA),
    };

    routes[`GET /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, grafana] } as Summary);
    routes[`PUT /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, grafana] });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}/edit`);

    const group = await screen.findByRole("group", { name: "Grafana alerts" });
    await waitFor(() => expect(within(group).getByLabelText("Credential")).toHaveValue(GRAFANA.credential_id));
    expect(within(group).getByLabelText(/^Folders/)).toHaveValue("Infrastructure");
    expect(within(group).getByLabelText("Rule labels value 1")).toHaveValue("web");

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));

    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources.map((x: { kind: string }) => x.kind)).toEqual(["prometheus", "grafana"]);
    expect(put.sources[1]).toEqual({ ...grafana, auth: sourceCredential(GRAFANA) });
  });

  it("adds a custom rule, previews it and sends it with the Prometheus source", async () => {
    let attempts = 0;
    routes["POST /api/projects/custom-rules/preview"] = () => json(customRulePreview);
    routes["POST /api/projects"] = () =>
      ++attempts === 1
        ? json(
            {
              type: "about:blank",
              title: "Request validation failed",
              status: 422,
              code: "validation_error",
              errors: [{ field: "body.sources.0.custom_rules.0.query", message: "the @ modifier is not allowed" }],
            },
            422,
          )
        : json({ ...base, project_id: "rules-id" }, 201);
    routes["GET /api/projects/rules-id"] = () => json({ ...base, project_id: "rules-id" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    const router = await renderAt("/projects/new");

    await fireEvent.update(await screen.findByLabelText("Name"), "Shop");
    await fireEvent.update(screen.getByLabelText("Label value 1"), "shop");
    await fireEvent.update(screen.getByLabelText("Label value 2"), "production");
    expect(screen.queryByRole("button", { name: "Add custom rule" })).not.toBeInTheDocument();
    await fireEvent.update(screen.getByLabelText("URL"), "http://vm.example:8428");
    await fireEvent.click(screen.getByRole("button", { name: "Add custom rule" }));
    const group = screen.getByRole("group", { name: "Prometheus-compatible metrics" });

    await fireEvent.update(within(group).getByLabelText("Title"), "E-mail queue depth");
    expect(within(group).getByLabelText("Identifier")).toHaveValue("e_mail_queue_depth");

    await fireEvent.update(within(group).getByLabelText("PromQL query"), "max by (job) (shop_email_queue_messages)");
    await fireEvent.update(within(group).getByLabelText("Finding when"), "threshold");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(within(group).getByLabelText("Threshold")).toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(within(group).getByLabelText("Threshold"), "500");
    await fireEvent.click(within(group).getByRole("button", { name: "Preview last 24 h" }));

    const preview = await within(group).findByRole("status");
    expect(preview).toHaveTextContent('max by (job) (shop_email_queue_messages{project="shop"})');
    expect(within(preview).getByRole("row", { name: /checkout-api/ })).toBeInTheDocument();
    const rule = {
      slug: "e_mail_queue_depth",
      title: "E-mail queue depth",
      description: "",
      query: "max by (job) (shop_email_queue_messages)",
      unit: "count",
      mode: "threshold",
      direction: "up",
      threshold: 500,
      critical_threshold: null,
      threshold_op: "above",
      min_duration_minutes: 15,
      min_change_factor: null,
      min_change: null,
      max_severity: null,
      identity_labels: [],
    };
    const previewed = JSON.parse(calls.find((c) => c.url.includes("custom-rules/preview"))?.body ?? "{}");
    expect(previewed.rule).toEqual(rule);
    expect(previewed.source.custom_rules).toEqual([]);

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await within(group).findByText("the @ modifier is not allowed")).toBeInTheDocument();

    await fireEvent.update(within(group).getByLabelText("PromQL query"), "max by (job) (shop_email_queue_messages)");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/rules-id"));
    const sent = JSON.parse(calls.filter((c) => c.method === "POST" && c.url === "/api/projects").at(-1)?.body ?? "{}");
    expect(sent.sources[0].custom_rules).toEqual([rule]);
  });

  it("deletes only after the name is typed and never while an analysis runs", async () => {
    let current: Summary = { ...base, report_count: 3, active_analysis: running };
    routes[`GET /api/projects/${PID}`] = () => json(current);
    routes[`DELETE /api/projects/${PID}`] = () => new Response(null, { status: 204 });

    const router = await renderAt(`/projects/${PID}/edit`);

    expect(await screen.findByText("3 saved reports")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete project…" })).toBeDisabled();

    current = { ...current, active_analysis: null };
    await router.push("/");
    await router.push(`/projects/${PID}/edit`);

    await waitFor(() => expect(screen.getByRole("button", { name: "Delete project…" })).toBeEnabled());

    await fireEvent.click(screen.getByRole("button", { name: "Delete project…" }));
    const confirm = screen.getByRole("button", { name: "Delete permanently" });
    await fireEvent.update(screen.getByLabelText(/to confirm/), "shop");

    expect(confirm).toBeDisabled();

    await fireEvent.update(screen.getByLabelText(/to confirm/), base.name);

    expect(confirm).toBeEnabled();

    await fireEvent.click(confirm);

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/"));
    expect(calls.some((c) => c.method === "DELETE" && c.url === `/api/projects/${PID}`)).toBe(true);
  });
});

describe("credentials", () => {
  it("lists credentials with their projects and creates one", async () => {
    await renderAt("/credentials");

    const row = await screen.findByRole("row", { name: new RegExp(VM.name) });
    expect(within(row).getByText("Bearer token")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: base.name })).toHaveAttribute("href", `/projects/${PID}`);
    expect(within(screen.getByRole("row", { name: new RegExp(GRAFANA.name) })).getByText("Not used")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Credentials" })).toHaveAttribute("href", "/credentials");

    await fireEvent.click(screen.getByRole("button", { name: "New credential" }));
    await fireEvent.click(screen.getByRole("button", { name: "Create credential" }));

    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Service account token")).toHaveAttribute("aria-invalid", "true");

    await fireEvent.update(screen.getByLabelText("Name"), " Sentry org ");
    await fireEvent.update(screen.getByLabelText("Source kind"), "sentry");
    await fireEvent.update(screen.getByLabelText("Auth token"), "sntrys-shared");
    await fireEvent.click(screen.getByRole("button", { name: "Create credential" }));

    expect(await screen.findByRole("row", { name: /Sentry org/ })).toBeInTheDocument();
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/credentials")?.body ?? "{}");
    expect(sent).toEqual({ name: "Sentry org", description: null, kind: "sentry", auth: { type: "token", token: "sntrys-shared" } });
  });

  it("keeps a stored secret on edit and explains why a used credential cannot be deleted", async () => {
    routes[`PUT /api/credentials/${VM.credential_id}`] = () => json(VM);
    routes[`DELETE /api/credentials/${VM.credential_id}`] = () =>
      problem(409, "credential_in_use", "Credential is in use", `Cannot delete it: used by ${base.name}.`);
    await renderAt("/credentials");

    await fireEvent.click(await screen.findByRole("button", { name: `Edit ${VM.name}` }));

    expect(screen.getByLabelText("Token")).toHaveAttribute("placeholder", "Stored — leave empty to keep");
    expect(screen.getByLabelText("Source kind")).toBeDisabled();

    await fireEvent.update(screen.getByLabelText("Authentication"), "basic");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    await fireEvent.update(screen.getByLabelText("Authentication"), "bearer");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}").auth).toEqual({ type: "bearer" });

    await fireEvent.click(screen.getByRole("button", { name: `Delete ${VM.name}` }));
    await fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(`Cannot delete it: used by ${base.name}.`);
  });
});

describe("project page", () => {
  it("shows configuration and pages through the analysis history", async () => {
    routes[`GET /api/projects/${PID}`] = () => json(base);
    routes[`GET /api/projects/${PID}/health`] = () => json([reachable]);
    routes["GET /api/analyses"] = (init) => {
      void init;
      const cursor = new URL(calls.at(-1)?.url ?? "", "http://x").searchParams.get("cursor");
      return cursor ? json({ items: [jobInterrupted], next_cursor: null }) : json({ items: [jobCompleted], next_cursor: jobCompleted.analysis_id });
    };

    await renderAt(`/projects/${PID}`);

    expect(await screen.findByRole("heading", { name: base.name })).toBeInTheDocument();
    expect(screen.getByText("victoriametrics.example:8428")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`Credential: ${VM.name}`))).toBeInTheDocument();
    expect(screen.getByText(/Mon, Wed, Fri at 08:00 \(Europe\/Berlin\)/)).toBeInTheDocument();
    expect(await screen.findAllByRole("row")).toHaveLength(2);

    await fireEvent.click(screen.getByRole("button", { name: "Load older analyses" }));

    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(3));
    expect(screen.queryByRole("button", { name: "Load older analyses" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes(`project_id=${PID}`))).toBe(true);
  });

  it("shows Cloudflare and Sentry filters as matcher chips", async () => {
    const cloudflare = { kind: "cloudflare", zone_id: "a".repeat(32), hostnames: ["shop.example.com", "api.example.com"], api_url: "https://api.cloudflare.com/client/v4", auth: { type: "none" } };
    const sentry = { kind: "sentry", organization: "acme", projects: ["shop-web"], tags: [{ key: "team", value: "shop" }], environment: "production", api_url: "https://sentry.io", tls_verify: true, auth: { type: "none" } };
    routes[`GET /api/projects/${PID}`] = () => json({ ...base, sources: [...base.sources, cloudflare, sentry] } as Summary);
    routes[`GET /api/projects/${PID}/health`] = () => json([reachable]);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });

    await renderAt(`/projects/${PID}`);

    const hostnames = await screen.findByRole("list", { name: "Cloudflare hostname filter" });
    expect(within(hostnames).getAllByRole("listitem").map((li) => li.textContent)).toEqual(['host=~"shop.example.com|api.example.com"']);
    const filters = screen.getByRole("list", { name: "Sentry filters" });
    expect(within(filters).getAllByRole("listitem").map((li) => li.textContent)).toEqual(['project="shop-web"', 'environment="production"', 'team="shop"']);
  });

  it("explains a deleted project", async () => {
    routes["GET /api/projects/gone"] = () => problem(404, "project_not_found", "Project not found");

    await renderAt("/projects/gone");

    expect(await screen.findByRole("alert")).toHaveTextContent("This project does not exist");
  });
});

describe("job progress", () => {
  it("shows stages, confirms and sends cancellation", async () => {
    const id = jobRunning.analysis_id;
    routes[`GET /api/analyses/${id}`] = () => json(jobRunning);
    routes[`DELETE /api/analyses/${id}`] = () => json({ ...jobRunning, state: "cancelled" });

    await renderAt(`/analyses/${id}`);

    const stages = await screen.findByRole("list");
    expect(within(stages).getByText(/Collect metrics/)).toHaveTextContent("(38/52)");

    await fireEvent.click(screen.getByRole("button", { name: "Cancel analysis" }));
    await fireEvent.click(screen.getByRole("button", { name: "Yes, cancel" }));

    await screen.findByText(/This analysis was cancelled/);
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
  });

  it("explains interruption by restart", async () => {
    routes[`GET /api/analyses/${jobInterrupted.analysis_id}`] = () => json(jobInterrupted);

    await renderAt(`/analyses/${jobInterrupted.analysis_id}`);

    expect(await screen.findByRole("alert")).toHaveTextContent("The service restarted while this analysis was running");
  });

  it("opens the report when it is available", async () => {
    routes[`GET /api/analyses/${jobCompleted.analysis_id}`] = () => json(jobCompleted);
    report(anomalies);

    const router = await renderAt(`/analyses/${jobCompleted.analysis_id}`);

    await waitFor(() => expect(router.currentRoute.value.path).toBe(`/reports/${anomalies.analysis_id}`));
  });
});

describe("report", () => {
  it("overview separates numerical findings from unverified AI hypotheses", async () => {
    await renderAt(report(anomalies));

    await screen.findByRole("button", { name: "Episodes" });
    expect(screen.getAllByText(/Hypothesis — unverified/)).toHaveLength(2);
    expect(screen.getByText(/Numerical findings are authoritative/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Coverage and limitations" })).toBeInTheDocument();
    expect(screen.getByText(/Recurring \(3 of 13 earlier days\)/)).toBeInTheDocument();
  });

  it("healthy report shows no-anomaly only together with coverage", async () => {
    await renderAt(report(healthy));

    expect(await screen.findByText(/No anomalies detected in evaluated signals/)).toBeInTheDocument();

    const coverage = screen.getByRole("heading", { name: "Coverage and limitations" }).closest("section") as HTMLElement;
    expect(within(coverage).getAllByText("Unsupported").length).toBeGreaterThan(0);
    expect(screen.getByText("No findings to explain.")).toBeInTheDocument();
  });

  it("AI failure keeps the numerical report", async () => {
    await renderAt(report(aiFailed));

    expect(await screen.findByText(/could not be generated \(timeout/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /CPU utilisation/ }).length).toBeGreaterThan(0);
  });

  it("partial report shows the banner and the omission", async () => {
    await renderAt(report(partial));

    expect(await screen.findByText(/Some signals could not be collected/)).toBeInTheDocument();
    expect(screen.getByText(/Network queries timed out/)).toBeInTheDocument();
    expect(screen.getByText(/AI explanations are off/)).toBeInTheDocument();
  });

  it("short history marks missing baselines and unsupported latency", async () => {
    const base = report(shortHistory);

    await renderAt(base);

    const coverage = (await screen.findByRole("heading", { name: "Coverage and limitations" })).closest("section") as HTMLElement;
    const latency = within(coverage).getByText("Latency").closest("tr") as HTMLElement;
    expect(latency).toHaveTextContent("Unsupported");
    expect(latency).toHaveTextContent(/histogram/i);

    await fireEvent.click(screen.getByRole("tab", { name: "Trends" }));

    const days = await screen.findByRole("group", { name: "Select a day" });
    expect(within(days).getAllByText("insufficient baseline").length).toBe(3);
    expect(within(days).getAllByText("insufficient data").length).toBe(9);
    expect(screen.getByText(/inconclusive/)).toBeInTheDocument();
  });

  it("findings filter through the URL and open accessible evidence", async () => {
    const router = await renderAt(`${report(anomalies)}/findings`);
    await screen.findByRole("heading", { name: "Findings" });

    await fireEvent.update(screen.getByLabelText("Category"), "client_errors");

    await waitFor(() => expect(router.currentRoute.value.query.category).toBe("client_errors"));
    expect(screen.queryByText(/CPU utilisation above/)).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("link", { name: /404 increase/ }));

    const panel = await screen.findByRole("region", { name: "404 increase" });
    expect(within(panel).getByRole("img", { name: /Evidence chart for 404 increase/ })).toBeInTheDocument();
    expect(within(panel).getByText(/rate\(http_server_request_duration_seconds_count/)).toBeInTheDocument();

    await fireEvent.click(within(panel).getByRole("button", { name: "Show data" }));

    expect(within(panel).getAllByText("gap").length).toBe(3);

    await fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(router.currentRoute.value.name).toBe("findings"));
    expect(router.currentRoute.value.query.category).toBe("client_errors");
  });

  it("findings expand in place like an accordion and collapse on a second click", async () => {
    const router = await renderAt(`${report(anomalies)}/findings`);
    const link = await screen.findByRole("link", { name: /404 increase/ });

    expect(link).toHaveAttribute("aria-expanded", "false");

    await fireEvent.click(link);

    const panel = await screen.findByRole("region", { name: "404 increase" });
    expect(link).toHaveAttribute("aria-expanded", "true");
    expect(link.closest("tr")!.nextElementSibling).toContainElement(panel);

    await fireEvent.click(link);

    await waitFor(() => expect(router.currentRoute.value.name).toBe("findings"));
    expect(screen.queryByRole("region", { name: "404 increase" })).not.toBeInTheDocument();
    expect(link).toHaveAttribute("aria-expanded", "false");
  });

  it("j/k move between findings and shortcuts are ignored in inputs", async () => {
    const first = anomalies.findings[0]!.finding_id;
    const router = await renderAt(`${report(anomalies)}/findings/${first}`);
    await screen.findByRole("region", { name: anomalies.findings[0]!.title });

    await fireEvent.keyDown(window, { key: "j" });

    await waitFor(() => expect(router.currentRoute.value.params.findingId).toBe(anomalies.findings[1]!.finding_id));

    await fireEvent.keyDown(screen.getByLabelText("Entity"), { key: "k" });

    expect(router.currentRoute.value.params.findingId).toBe(anomalies.findings[1]!.finding_id);
  });

  it("trends drill into a day and list recurring problems", async () => {
    await renderAt(`${report(anomalies)}/trends`);
    const days = await screen.findByRole("group", { name: "Select a day" });
    const buttons = within(days).getAllByRole("button");

    expect(buttons).toHaveLength(14);

    await fireEvent.click(buttons[buttons.length - 1]!);

    expect(await screen.findByRole("heading", { name: /2 episodes/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recurring problems" })).toHaveTextContent("client_error_rate");
  });

  it("renders a Cloudflare report with its edge findings", async () => {
    await renderAt(report(cloudflareReport));

    expect(await screen.findByText("Synthetic data")).toBeInTheDocument();
    expect((await screen.findAllByText(/Origin error rate \(520-530\)/)).length).toBeGreaterThan(0);
  });

  it("renders a Sentry report with its source and application findings", async () => {
    await renderAt(`${report(sentryReport)}/findings`);

    expect((await screen.findAllByText("Sentry")).length).toBeGreaterThan(0);
    const links = await screen.findAllByRole("link", { name: /Error spike/ });
    expect(links).toHaveLength(2);
    expect(links.every((link) => link.closest("tr")?.textContent?.includes("Sentry"))).toBe(true);
    expect(links.some((link) => link.closest("tr")?.textContent?.includes("TypeError"))).toBe(true);
  });

  it("a Sentry project's error spike names its errors in the row and lists them in the detail", async () => {
    const total = sentryReport.findings.find((f) => f.signal === "app_error_rate" && !("issue" in f.entity.labels));
    await renderAt(`${report(sentryReport)}/findings/${total?.finding_id ?? ""}`);

    const link = await screen.findByRole("link", { name: /Error spike/, current: true });
    expect(link.closest("tr")).toHaveTextContent("shop-api (production) · TypeError: 'NoneType' object is not subscriptable +4");

    const panel = await screen.findByRole("region", { name: "Errors" });
    const rows = within(panel).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("TypeError: 'NoneType' object is not subscriptable");
    expect(rows[1]).toHaveTextContent("SHOP-API-4");
    expect(rows[1]).toHaveTextContent("94%");
    expect(rows.at(-1)).toHaveTextContent("Other errors");
    expect(within(panel).queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders a Wazuh report with agent findings", async () => {
    await renderAt(`${report(wazuhReport)}/findings`);

    const link = await screen.findByRole("link", { name: /Surge of authentication failures/ });
    expect(link.closest("tr")).toHaveTextContent("Wazuh");
    expect(link.closest("tr")).toHaveTextContent("shop-db-1");
  });

  it("renders a Grafana report with alert rule findings", async () => {
    await renderAt(`${report(grafanaReport)}/findings`);

    const links = await screen.findAllByRole("link", { name: /Critical Grafana alert firing/ });
    expect(links).toHaveLength(2);
    const row = links[0]?.closest("tr");
    expect(row).toHaveTextContent("Grafana");
    expect(row).toHaveTextContent("Checkout 5xx errors");
    expect(row).toHaveTextContent("shop-web-1, shop-web-2");
  });

  it("a Grafana finding lists its alert instances with labels and annotations", async () => {
    const critical = grafanaReport.findings.find((f) => f.signal === "alert_firing_critical" && f.state === "resolved");
    await renderAt(`${report(grafanaReport)}/findings/${critical?.finding_id ?? ""}`);

    const panel = await screen.findByRole("region", { name: "Alert instances" });
    expect(within(panel).getByText("shop-web-1")).toBeInTheDocument();
    expect(within(panel).getByText("shop-web-1 answers more than 5% of requests with 5xx")).toBeInTheDocument();
    const labels = within(panel).getByRole("list", { name: "Labels of shop-web-1" });
    expect(within(labels).getByText("instance=shop-web-1")).toHaveClass("key");
    expect(within(labels).getByText("env=production")).not.toHaveClass("key");
  });

  it("a finding explains the problem and what to check", async () => {
    const clientErrors = anomalies.findings.find((f) => f.signal === "client_error_rate");
    await renderAt(`${report(anomalies)}/findings/${clientErrors?.finding_id ?? ""}`);

    const details = await screen.findByRole("region", { name: "Finding details" });
    expect(details).toHaveTextContent(clientErrors?.summary ?? "missing summary");
    const help = within(details).getByRole("region", { name: "What to check" });
    expect(help).toHaveTextContent(clientErrors?.help ?? "missing help");
  });

  it("a custom rule finding shows the rule, its description and its threshold", async () => {
    const queue = customRulesReport.findings.find((f) => f.signal === "custom_email_queue");
    await renderAt(`${report(customRulesReport)}/findings/${queue?.finding_id ?? ""}`);

    const details = await screen.findByRole("region", { name: "Finding details" });
    expect(details).toHaveTextContent("Messages waiting for the mail relay");
    expect(details).toHaveTextContent("E-mail queue depth · ≥ 500 for 10 min");
    expect(details).toHaveTextContent("2000 (set in the custom rule)");
    expect(details).not.toHaveTextContent("not an SLO");
  });

  it("findings show the source each one came from", async () => {
    await renderAt(`${report(anomalies)}/findings`);

    const link = await screen.findByRole("link", { name: /404 increase/ });
    expect(link.closest("tr")).toHaveTextContent("Prometheus");
  });

  it("findings filter by source through the URL", async () => {
    const router = await renderAt(`${report(anomalies)}/findings`);
    await screen.findByRole("heading", { name: "Findings" });
    const select = screen.getByLabelText("Source");

    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["All", "Prometheus"]);

    await fireEvent.update(select, "prometheus");

    await waitFor(() => expect(router.currentRoute.value.query.source).toBe("prometheus"));
    expect(screen.getByRole("link", { name: /404 increase/ })).toBeInTheDocument();
  });

  it("explains unavailable and incompatible reports", async () => {
    routes["GET /api/analyses/gone/report"] = () => problem(404, "report_unavailable", "No report", "Analysis cancelled.");

    await renderAt("/reports/gone");

    expect(await screen.findByRole("alert")).toHaveTextContent("No report was saved for this analysis");

    routes["GET /api/analyses/old/report"] = () => problem(404, "schema_unsupported", "Unsupported", "Saved with schema 2.0.");
  });

  it("overview leads with the worst finding, blind spots and a timeline", async () => {
    await renderAt(report(anomalies));

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("CPU on node · shop-production peaked at 96.3 % over 3 h");
    expect(screen.getByText(/4\.4× the usual 22\.0 %/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Summary" })).toHaveTextContent("Containers (no metrics found) is not evaluated");
    expect(screen.getByRole("region", { name: "Blind spots" })).toHaveTextContent("Containers");

    const timeline = screen.getByRole("region", { name: "State timeline" });
    expect(within(timeline).getAllByRole("link")).toHaveLength(anomalies.findings.length);
  });

  it("overview totals the latest day's Sentry errors against the days before", async () => {
    await renderAt(report(sentryReport));

    const errors = await screen.findByRole("region", { name: "Sentry error events" });
    expect(errors).toHaveTextContent("13,557");
    expect(errors).toHaveTextContent("+57 % vs. median 8,618/day over 13 days before");
    expect(within(errors).getByRole("img")).toHaveAccessibleName("Sentry error events per day, last 14 days");
  });

  it("overview totals Wazuh alerts", async () => {
    await renderAt(report(wazuhReport));

    expect(await screen.findByRole("region", { name: "Wazuh alerts" })).toHaveTextContent("7,943");
  });

  it("overview totals Grafana alert firings over the selected rules", async () => {
    await renderAt(report(grafanaReport));

    expect(await screen.findByRole("region", { name: "Grafana alert firings" })).toHaveTextContent("4 alert rules");
  });

  it("overview has no event totals without such sources", async () => {
    await renderAt(report(anomalies));

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("region", { name: /Sentry error events|Wazuh alerts|Grafana alert firings/ })).not.toBeInTheDocument();
  });

  it("timeline tab shows every finding with its source", async () => {
    await renderAt(`${report(anomalies)}/timeline`);

    const timeline = await screen.findByRole("region", { name: "State timeline" });

    expect(within(timeline).getAllByRole("link")).toHaveLength(anomalies.findings.length);
    expect(within(timeline).getAllByText(/^Prometheus:/)).toHaveLength(anomalies.findings.length);
    expect(within(screen.getByRole("group", { name: "Sources" })).getByRole("button", { name: /All/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("timeline filters by source through the URL", async () => {
    const mixed = {
      ...anomalies,
      analysis_id: "mixed",
      findings: [...anomalies.findings, ...sentryReport.findings],
      capabilities: [...anomalies.capabilities, ...sentryReport.capabilities],
    };
    const router = await renderAt(`${report(mixed)}/timeline`);
    const group = await screen.findByRole("group", { name: "Sources" });
    const lanes = () => within(screen.getByRole("region", { name: "State timeline" })).queryAllByRole("link");

    expect(lanes()).toHaveLength(mixed.findings.length);

    await fireEvent.click(within(group).getByRole("button", { name: /Prometheus/ }));

    await waitFor(() => expect(router.currentRoute.value.query.source).toBe("sentry"));
    expect(lanes()).toHaveLength(sentryReport.findings.length);
    expect(within(group).getByRole("button", { name: /All/ })).toHaveAttribute("aria-pressed", "false");

    await fireEvent.click(within(group).getByRole("button", { name: /Sentry/ }));

    await waitFor(() => expect(router.currentRoute.value.query.source).toBe(""));
    expect(screen.getByText("No sources selected.")).toBeInTheDocument();

    await fireEvent.click(within(group).getByRole("button", { name: /All/ }));

    await waitFor(() => expect(router.currentRoute.value.query.source).toBeUndefined());
    expect(lanes()).toHaveLength(mixed.findings.length);
  });

  it("findings filter by recurrence through the URL", async () => {
    const router = await renderAt(`${report(anomalies)}/findings`);
    const group = await screen.findByRole("group", { name: "Recurrence" });

    await fireEvent.click(within(group).getByRole("button", { name: /New today/ }));

    await waitFor(() => expect(router.currentRoute.value.query.recurrence).toBe("new"));
    expect(screen.queryByRole("link", { name: /404 increase/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /CPU utilisation/ })).toBeInTheDocument();
  });

  it("trends state the direction and the week-over-week numbers", async () => {
    await renderAt(`${report(anomalies)}/trends`);

    expect(await screen.findByRole("heading", { name: "Getting worse: more anomalies in the last 7 days" })).toBeInTheDocument();
    expect(screen.getByText("1.79 %")).toBeInTheDocument();
    expect(screen.getByText("The latest day is the worst of the 14.")).toBeInTheDocument();
  });

  it("trends open on the latest day", async () => {
    await renderAt(`${report(anomalies)}/trends`);

    const days = await screen.findByRole("group", { name: "Select a day" });
    const buttons = within(days).getAllByRole("button");

    expect(buttons[buttons.length - 1]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: /^Latest day, .* · 2 episodes$/ })).toBeInTheDocument();
  });

  it("overview rows collapse and expand", async () => {
    await renderAt(report(anomalies));
    const toggle = await screen.findByRole("button", { name: "Episodes" });

    expect(screen.getByRole("region", { name: "State timeline" })).toBeVisible();

    await fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("region", { name: "State timeline", hidden: true })).not.toBeVisible();
  });

  it("tabs follow the arrow-key pattern", async () => {
    const router = await renderAt(report(anomalies));
    const overview = await screen.findByRole("tab", { name: "Overview" });

    expect(overview).toHaveAttribute("aria-selected", "true");

    await fireEvent.keyDown(overview, { key: "ArrowRight" });

    await waitFor(() => expect(router.currentRoute.value.name).toBe("findings"));
    await waitFor(() => expect(screen.getByRole("tab", { name: /Findings/ })).toHaveFocus());
  });

  it("scope tab shows what each filter part selected", async () => {
    await renderAt(`${report(grafanaReport)}/scope`);

    const panel = await screen.findByRole("region", { name: "Grafana" });
    const checks = within(panel).getByRole("table", { name: "Filter checks of Grafana" });

    expect(within(panel).getByText(/4 of 5 rules/)).toBeInTheDocument();
    expect(within(checks).getAllByRole("row")).toHaveLength(1 + (grafanaReport.diagnostics[0]?.checks.length ?? 0));
    expect(within(checks).getByRole("rowheader", { name: /Folder Shop/ })).toBeInTheDocument();
    expect(within(checks).getAllByText("OK")).toHaveLength(2);
    expect(within(panel).getByRole("table", { name: "Selected by Grafana" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Scope" })).toHaveAttribute("aria-selected", "true");
  });

  it("scope tab flags a filter part that excludes all data", async () => {
    const [diagnostics] = sentryReport.diagnostics;
    const env = { part: "prod", field: "environment", matched: 0, matched_without: 432, unit: "events", available: ["production", "staging"], available_truncated: false, matching: [] };
    const wrong = { ...sentryReport, analysis_id: "wrong-env", diagnostics: [{ ...diagnostics, checks: [env] }] };

    await renderAt(`${report(wrong)}/scope`);

    const row = await screen.findByRole("row", { name: /Environment prod/ });
    expect(within(row).getByText("Excludes all data")).toHaveClass("v-critical");
    expect(within(row).getByText("432 events")).toBeInTheDocument();
    expect(within(row).getByText("production")).not.toHaveClass("on");
    expect(screen.getByRole("tab", { name: "Scope 1" })).toBeInTheDocument();
  });

  it("a finding lists related metrics with resource, similarity and timing kept apart", async () => {
    const errors = relatedReport.findings.find((f) => f.signal === "server_error_ratio")!;
    const deadlocks = relatedReport.findings.find((f) => f.signal === "database_deadlocks")!;
    const router = await renderAt(`${report(relatedReport)}/findings/${errors.finding_id}`);

    const panel = await screen.findByRole("region", { name: "Related metrics" });
    const latency = within(panel).getByRole("button", { name: "p95 latency" });
    expect(latency).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByText("Same resource")).toBeInTheDocument();
    expect(within(panel).getByText("moves together · r = +0.83")).toBeInTheDocument();
    expect(within(panel).getAllByText("began about 10 min earlier · no clear lead or lag").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/has no finding of its own/)).toBeInTheDocument();
    expect(within(panel).getByRole("img", { name: /p95 latency .* on one time axis/ })).toBeInTheDocument();

    await fireEvent.click(within(panel).getByRole("button", { name: "Show data" }));

    expect(within(panel).getByRole("columnheader", { name: "p95 latency" })).toBeInTheDocument();

    await fireEvent.click(within(panel).getByRole("button", { name: "Deviation from usual" }));

    expect(within(panel).getByText(/robust z: distance from the usual median/)).toBeInTheDocument();

    await fireEvent.click(within(panel).getByRole("button", { name: "Deadlocks" }));

    expect(within(panel).getAllByText("Unknown association").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/No verified connection between these resources/)).toBeInTheDocument();

    await fireEvent.click(within(panel).getByRole("link", { name: "Open" }));

    await waitFor(() => expect(router.currentRoute.value.params.findingId).toBe(deadlocks.finding_id));
  });

  it("related metrics explain old reports", async () => {
    await renderAt(`${report(anomalies)}/findings/${anomalies.findings[0]!.finding_id}`);

    const panel = await screen.findByRole("region", { name: "Related metrics" });

    expect(within(panel).getByText(/predates related metrics/)).toBeInTheDocument();
  });

  it("related metrics never read an empty result as healthy", async () => {
    const related = relatedReport.related_metrics;
    const empty = {
      ...relatedReport,
      analysis_id: "no-related",
      related_metrics: {
        ...related,
        comparisons: [],
        evaluations: related.evaluations.map((e) => ({ ...e, retained: 0, omitted: 0, candidates: 0, evaluated: 0 })),
      },
    };
    const errors = empty.findings.find((f) => f.signal === "server_error_ratio")!;
    await renderAt(`${report(empty)}/findings/${errors.finding_id}`);

    const panel = await screen.findByRole("region", { name: "Related metrics" });

    expect(within(panel).getByText("No other collected metric had an unusual change around this finding.")).toBeInTheDocument();
    expect(within(panel).getByText(/0 with an unusual change in the window · 0 compared/)).toBeInTheDocument();
    expect(within(panel).queryByRole("table")).not.toBeInTheDocument();
  });

  it("scope tab explains old reports and shows failed sources", async () => {
    await renderAt(`${report(healthy)}/scope`);

    expect(await screen.findByText(/predates scope diagnostics/)).toBeInTheDocument();

    const failed = {
      ...sentryReport,
      analysis_id: "failed-source",
      exclusions: [{ code: "source_unavailable", message: "prometheus source failed (timeout): no answer", family: "cpu" }],
    };
    await renderAt(`${report(failed)}/scope`);

    const panel = await screen.findByRole("region", { name: "Prometheus" });
    expect(within(panel).getByText("prometheus source failed (timeout): no answer")).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "Sentry" })).toHaveLength(1);
  });
});

describe("theme", () => {
  it("switches between auto, light and dark and remembers the choice", async () => {
    await renderAt("/");
    const group = await screen.findByRole("group", { name: "Theme" });

    expect(within(group).getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");

    await fireEvent.click(within(group).getByRole("button", { name: "Dark" }));

    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem("assistant.theme")).toBe("dark");

    await fireEvent.click(within(group).getByRole("button", { name: "Auto" }));

    await waitFor(() => expect(document.documentElement.dataset.theme).toBeUndefined());
    expect(localStorage.getItem("assistant.theme")).toBeNull();
  });
});
