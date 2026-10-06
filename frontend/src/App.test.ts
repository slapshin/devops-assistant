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
import projects from "../../fixtures/api/projects.json";
import aiFailed from "../../fixtures/reports/report_ai_failed.json";
import anomalies from "../../fixtures/reports/report_anomalies.json";
import cloudflareReport from "../../fixtures/reports/report_cloudflare.json";
import healthy from "../../fixtures/reports/report_healthy.json";
import partial from "../../fixtures/reports/report_partial_source_error.json";
import shortHistory from "../../fixtures/reports/report_short_history.json";
import type { AnalysisJob, ProjectSummary } from "./api/client";
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

beforeEach(() => {
  calls = [];
  routes = {
    "GET /api/config": () => json(config),
    "GET /api/projects": () => json(projects),
  };
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
  it("shows health, latest report, trend and empty states for every project", async () => {
    const items = [
      withId("ok"),
      withId("down", { latest_analysis: null }),
      withId("denied"),
      withId("empty-match"),
      withId("no-source", { sources: [] }),
      withId("locked", { credentials_readable: false }),
    ];
    routes["GET /api/projects"] = () => json({ items });
    routes["GET /api/projects/ok/health"] = () => json([reachable]);
    routes["GET /api/projects/down/health"] = () => json([{ ...reachable, reachable: false, auth_ok: null, matched_series: null }]);
    routes["GET /api/projects/denied/health"] = () => json([{ ...reachable, auth_ok: false, matched_series: null }]);
    routes["GET /api/projects/empty-match/health"] = () => json([{ ...reachable, matched_series: 0 }]);
    await renderAt("/");

    const card = (name: string) => screen.getByRole("article", { name });
    await waitFor(() => expect(within(card("ok")).getByText(/Reachable/)).toBeInTheDocument());
    expect(await within(card("down")).findByText(/Unreachable/)).toBeInTheDocument();
    expect(await within(card("denied")).findByText(/Auth failed/)).toBeInTheDocument();
    expect(await within(card("empty-match")).findByText(/No matching series/)).toBeInTheDocument();
    expect(within(card("no-source")).getByText(/No source/)).toBeInTheDocument();
    expect(within(card("locked")).getByText(/Credentials unreadable/)).toBeInTheDocument();
    // health is never requested for projects that cannot be checked
    expect(calls.some((c) => c.url.includes("no-source/health") || c.url.includes("locked/health"))).toBe(false);

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
    await fireEvent.update(screen.getByLabelText("Authentication"), "bearer");
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(screen.getByLabelText("Token")).toHaveAttribute("aria-invalid", "true");
    await fireEvent.update(screen.getByLabelText("Token"), "tok");
    expect(screen.getByText("The Prometheus connection has not been tested.")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/new-id"));
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({
      name: "Shop",
      description: null,
      matchers: [
        { name: "project", value: "shop" },
        { name: "env", value: "prod" },
      ],
      sources: [{ kind: "prometheus", url: "http://vm:8428/prom", tls_verify: true, auth: { type: "bearer", token: "tok" } }],
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
    expect(screen.getByLabelText("Reports to keep")).toHaveValue(30);

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

  it("keeps a stored secret when the field is left empty and shows server errors", async () => {
    routes[`GET /api/projects/${PID}`] = () => json(base);
    let attempt = 0;
    routes[`PUT /api/projects/${PID}`] = () =>
      ++attempt === 1
        ? json({ type: "about:blank", title: "Request validation failed", status: 422, code: "validation_error", errors: [{ field: "body.matchers.0.name", message: "bad label" }] }, 422)
        : json(base);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    await renderAt(`/projects/${PID}/edit`);
    expect(await screen.findByLabelText("Token")).toHaveAttribute("placeholder", "Stored — leave empty to keep");
    expect(screen.getByLabelText("Name")).toHaveValue(base.name);

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("bad label")).toBeInTheDocument();
    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources[0].auth).toEqual({ type: "bearer" });

    // switching the auth type needs the new secret
    await fireEvent.update(screen.getByLabelText("Authentication"), "basic");
    expect(screen.getByLabelText("Password")).toHaveAttribute("placeholder", "");
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("clones a project with all settings and its stored secret", async () => {
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
    expect(screen.getByLabelText("Token")).toHaveAttribute("placeholder", "Stored — leave empty to keep");
    expect(screen.queryByRole("heading", { name: "Delete project" })).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Test Prometheus connection" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/projects/test-connection")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/projects/test-connection")?.body ?? "{}").project_id).toBe(PID);

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/clone-id"));
    const post = calls.find((c) => c.method === "POST" && c.url.startsWith("/api/projects?"));
    expect(post?.url).toBe(`/api/projects?clone_of=${PID}`);
    const sent = JSON.parse(post?.body ?? "{}");
    expect(sent.matchers).toEqual(base.matchers);
    expect(sent.sources[0].auth).toEqual({ type: "bearer" });
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
      project_id: null,
      matchers: [
        { name: "project", value: "shop" },
        { name: "env", value: "production" },
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
    expect(screen.getByLabelText("API token")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Label value 1")).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("The Cloudflare connection has not been tested.")).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("Zone ID"), "0123456789ABCDEF0123456789ABCDEF");
    await fireEvent.update(screen.getByLabelText(/Hostnames/), "Shop.Example.com api.example.com");
    await fireEvent.update(screen.getByLabelText("API token"), "cf-token");
    await fireEvent.click(screen.getByRole("button", { name: "Test Cloudflare connection" }));
    const result = await screen.findByRole("region", { name: "Connection test result" });
    expect(result).toHaveTextContent("Reachable · 3,456,789 requests in 24 h · 30 days of history");
    expect(within(result).getByRole("row", { name: /Security \(WAF\).*Supported/ })).toBeInTheDocument();
    const source = { kind: "cloudflare", zone_id: "0123456789abcdef0123456789abcdef", hostnames: ["api.example.com", "shop.example.com"], api_url: "https://api.cloudflare.com/client/v4/graphql", api_token: "cf-token" };
    expect(JSON.parse(calls.find((c) => c.url.includes("test-connection"))?.body ?? "{}")).toEqual({ project_id: null, matchers: [], source });

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(router.currentRoute.value.path).toBe("/projects/cf-id"));
    const sent = JSON.parse(calls.find((c) => c.method === "POST" && c.url === "/api/projects")?.body ?? "{}");
    expect(sent.matchers).toEqual([]);
    expect(sent.sources).toEqual([source]);
  });

  it("keeps a stored Cloudflare token and maps server errors to the right source", async () => {
    const cloudflare = { kind: "cloudflare", zone_id: "0123456789abcdef0123456789abcdef", hostnames: ["shop.example.com"], api_url: "https://api.cloudflare.com/client/v4/graphql", token_set: true };
    const both = { ...base, sources: [...base.sources, cloudflare] } as Summary;
    routes[`GET /api/projects/${PID}`] = () => json(both);
    routes[`PUT /api/projects/${PID}`] = () =>
      json({ type: "about:blank", title: "Request validation failed", status: 422, code: "validation_error", errors: [{ field: "body.sources.1.zone_id", message: "zone rejected" }] }, 422);
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    await renderAt(`/projects/${PID}/edit`);
    expect(await screen.findByLabelText("API token")).toHaveAttribute("placeholder", "Stored — leave empty to keep");
    expect(screen.getByLabelText(/Hostnames/)).toHaveValue("shop.example.com");

    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("zone rejected")).toBeInTheDocument();
    expect(screen.getByLabelText("Zone ID")).toHaveAttribute("aria-invalid", "true");
    const put = JSON.parse(calls.find((c) => c.method === "PUT")?.body ?? "{}");
    expect(put.sources.map((x: { kind: string }) => x.kind)).toEqual(["prometheus", "cloudflare"]);
    expect(put.sources[1]).not.toHaveProperty("api_token");
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
    expect(screen.getByText(/Bearer token/)).toBeInTheDocument();
    expect(screen.getByText(/Mon, Wed, Fri at 08:00 \(Europe\/Berlin\)/)).toBeInTheDocument();
    expect(await screen.findAllByRole("row")).toHaveLength(2);
    await fireEvent.click(screen.getByRole("button", { name: "Load older analyses" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(3));
    expect(screen.queryByRole("button", { name: "Load older analyses" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes(`project_id=${PID}`))).toBe(true);
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

  it("renders a Cloudflare report with its source and edge findings", async () => {
    await renderAt(report(cloudflareReport));
    expect(await screen.findByText("Cloudflare")).toBeInTheDocument();
    expect(screen.getByText("Synthetic data")).toBeInTheDocument();
    expect((await screen.findAllByText(/Origin error rate \(520-530\)/)).length).toBeGreaterThan(0);
  });

  it("findings show the source each one came from", async () => {
    await renderAt(`${report(anomalies)}/findings`);
    const link = await screen.findByRole("link", { name: /404 increase/ });
    expect(link.closest("tr")).toHaveTextContent("Prometheus");
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
