import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { render, screen } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory } from "vue-router";
import config from "../../fixtures/api/config.json";
import App from "./App.vue";
import { makeRouter } from "./router";

async function renderAt(path: string) {
  const router = makeRouter(createMemoryHistory());
  await router.push(path);
  await router.isReady();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(App, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  });
}

function mockFetch(response: Response | Error) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => (response instanceof Error ? Promise.reject(response) : Promise.resolve(response))),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("foundation routes", () => {
  it("shows backend configuration on the start page", async () => {
    mockFetch(new Response(JSON.stringify(config), { headers: { "content-type": "application/json" } }));
    await renderAt("/");
    expect(await screen.findByText("http://localhost:8428")).toBeInTheDocument();
    expect(screen.getByText(/openai · not_configured/)).toBeInTheDocument();
  });

  it("reports an unreachable backend with a retry action", async () => {
    mockFetch(new TypeError("network down"));
    await renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Lost connection to the assistant");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it.each([
    ["/reports/abc", "Overview"],
    ["/reports/abc/findings", "Findings"],
    ["/reports/abc/findings/fnd_1", "Evidence"],
    ["/reports/abc/trends", "Trends"],
    ["/analyses/abc", "Job progress"],
  ])("marks %s as not implemented", async (path, view) => {
    await renderAt(path);
    expect(screen.getByRole("heading", { name: view })).toBeInTheDocument();
    expect(screen.getByText(/Not implemented yet — delivered by T008/)).toBeInTheDocument();
  });
});
