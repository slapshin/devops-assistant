import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory } from "vue-router";
import connectionTest from "../../../../fixtures/api/connection_test.json";
import { makeRouter } from "../../router";
import ProjectFormView from "./ProjectFormView.vue";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

let respond: (url: string, init?: RequestInit) => Response | Promise<Response>;

beforeEach(() => {
  respond = () => json({ items: [] });
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => vi.unstubAllGlobals());

async function renderForm() {
  const router = makeRouter(createMemoryHistory());
  await router.push("/projects/new");
  await router.isReady();
  render(ProjectFormView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }]],
    },
  });
  await fireEvent.update(screen.getByLabelText("Label value 1"), "shop");
  await fireEvent.update(screen.getByLabelText("Label value 2"), "prod");
}

describe("project source requests during edits", () => {
  it("retains a pending connection result and marks it stale against the current draft", async () => {
    const pending = deferredResponse();
    respond = (url) => url === "/api/projects/test-connection" ? pending.promise : json({ items: [] });
    await renderForm();
    await fireEvent.update(screen.getByLabelText("URL"), "synthetic://healthy");

    await fireEvent.click(screen.getByRole("button", { name: "Test Prometheus connection" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Testing…" })).toBeDisabled());

    await fireEvent.update(screen.getByLabelText("URL"), "synthetic://incident");
    pending.resolve(json(connectionTest));

    expect(await screen.findByRole("region", { name: "Connection test result" })).toHaveTextContent("Reachable");
    expect(screen.getByText(/The form changed after this test/)).toBeInTheDocument();
    expect(screen.getByText("The Prometheus connection was not tested with the current values.")).toBeInTheDocument();

    await fireEvent.update(screen.getByLabelText("URL"), " synthetic://healthy ");

    await waitFor(() => expect(screen.queryByText(/The form changed after this test/)).not.toBeInTheDocument());
  });

  it("shows a late save error on the submitted source after another source is enabled", async () => {
    const pending = deferredResponse();
    respond = (url, init) => url === "/api/projects" && init?.method === "POST" ? pending.promise : json({ items: [] });
    await renderForm();
    await fireEvent.update(screen.getByLabelText("Name"), "Shop");
    await fireEvent.click(screen.getByLabelText("Analyse a Cloudflare zone"));
    await fireEvent.update(screen.getByLabelText("Zone ID"), "a".repeat(32));
    await fireEvent.update(screen.getByLabelText("API URL"), "synthetic://healthy");

    await fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/projects", expect.objectContaining({ method: "POST" })));

    await fireEvent.update(screen.getByLabelText("URL"), "synthetic://healthy");
    pending.resolve(json({
      type: "about:blank", title: "Invalid", status: 422, code: "validation_error",
      errors: [{ field: "body.sources.0.zone_id", message: "Zone rejected" }],
    }, 422));

    expect(await screen.findByText("Zone rejected")).toBeInTheDocument();
    expect(screen.getByLabelText("Zone ID")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("URL")).toHaveAttribute("aria-invalid", "false");
  });
});
