import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import config from "../../fixtures/api/config.json";
import { routes } from "./App";

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
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
    renderAt("/");
    expect(await screen.findByText("http://localhost:8428")).toBeInTheDocument();
    expect(screen.getByText(/openai · not_configured/)).toBeInTheDocument();
  });

  it("reports an unreachable backend with a retry action", async () => {
    mockFetch(new TypeError("network down"));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Lost connection to the assistant");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it.each([
    ["/reports/abc", "Overview"],
    ["/reports/abc/findings", "Findings"],
    ["/reports/abc/findings/fnd_1", "Evidence"],
    ["/reports/abc/trends", "Trends"],
    ["/analyses/abc", "Job progress"],
  ])("marks %s as not implemented", (path, view) => {
    renderAt(path);
    expect(screen.getByRole("heading", { name: view })).toBeInTheDocument();
    expect(screen.getByText(/Not implemented yet — delivered by T008/)).toBeInTheDocument();
  });
});
