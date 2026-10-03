import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, type Router } from "vue-router";
import config from "../../fixtures/api/config.json";
import jobCompleted from "../../fixtures/jobs/job_completed.json";
import jobInterrupted from "../../fixtures/jobs/job_interrupted.json";
import jobRunning from "../../fixtures/jobs/job_running.json";
import aiFailed from "../../fixtures/reports/report_ai_failed.json";
import anomalies from "../../fixtures/reports/report_anomalies.json";
import healthy from "../../fixtures/reports/report_healthy.json";
import partial from "../../fixtures/reports/report_partial_source_error.json";
import shortHistory from "../../fixtures/reports/report_short_history.json";
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
    "GET /api/discovery/projects": () => json({ items: [{ project: "paas" }, { project: "pw" }], source_status: { reachable: true } }),
    "GET /api/discovery/projects/paas/envs": () => json({ project: "paas", items: [{ env: "production" }] }),
    "GET /api/discovery/projects/pw/envs": () => json({ project: "pw", items: [{ env: "development" }, { env: "production" }] }),
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

describe("start", () => {
  it("resets env when the project changes and ignores stale env responses", async () => {
    let releasePw: (r: Response) => void = () => {};
    routes["GET /api/discovery/projects/pw/envs"] = () => new Promise<Response>((resolve) => (releasePw = resolve));
    await renderAt("/");
    const project = await screen.findByLabelText("Project");
    await fireEvent.update(project, "pw");
    await fireEvent.update(project, "paas");
    // the single paas env is selected explicitly
    await waitFor(() => expect((screen.getByLabelText("Environment") as HTMLSelectElement).value).toBe("production"));
    releasePw(json({ project: "pw", items: [{ env: "development" }, { env: "staging" }] }));
    await new Promise((r) => setTimeout(r, 20));
    const options = within(screen.getByLabelText("Environment")).getAllByRole("option").map((o) => o.textContent);
    expect(options).not.toContain("staging");
    expect((screen.getByLabelText("Environment") as HTMLSelectElement).value).toBe("production");
  });

  it("requires an env before analysing and shows queue-full with retry time", async () => {
    routes["POST /api/analyses"] = () => problem(429, "queue_full", "Analysis queue is full", undefined, { "retry-after": "45" });
    routes["GET /api/analyses"] = () => json({ items: [], next_cursor: null });
    await renderAt("/");
    await fireEvent.update(await screen.findByLabelText("Project"), "pw");
    const analyze = screen.getByRole("button", { name: "Analyze" });
    expect(analyze).toBeDisabled();
    await screen.findByRole("option", { name: "development" });
    await fireEvent.update(screen.getByLabelText("Environment"), "production");
    await waitFor(() => expect(analyze).toBeEnabled());
    await fireEvent.click(analyze);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 45 seconds");
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({ project: "pw", env: "production" });
  });

  it("reports an unreachable source", async () => {
    routes["GET /api/discovery/projects"] = () => problem(503, "metrics_source_unavailable", "Metrics source unavailable", "cannot reach source");
    await renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Metrics source unreachable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
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
    await screen.findByText("Top findings (latest 24 h)");
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
    await screen.findByRole("heading", { name: "Findings in the latest 24 hours" });
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
    expect(await screen.findByRole("heading", { name: /2 episode\(s\)/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Recurring/ }).parentElement).toHaveTextContent("client_error_rate");
  });

  it("explains unavailable and incompatible reports", async () => {
    routes["GET /api/analyses/gone/report"] = () => problem(404, "report_unavailable", "No report", "Analysis cancelled.");
    await renderAt("/reports/gone");
    expect(await screen.findByRole("alert")).toHaveTextContent("No report was saved for this analysis");
    routes["GET /api/analyses/old/report"] = () => problem(404, "schema_unsupported", "Unsupported", "Saved with schema 2.0.");
  });

  it("overview leads with the worst finding, blind spots and a timeline", async () => {
    await renderAt(report(anomalies));
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("CPU on node · paas-production peaked at 96.3 % over 3 h");
    expect(screen.getByText(/4\.4× the usual 22\.0 %/)).toBeInTheDocument();
    expect(screen.getByText("Blind spots").parentElement).toHaveTextContent("Containers (no metrics found) is not evaluated");
    const timeline = screen.getByRole("region", { name: "When it happened" });
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
