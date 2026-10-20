import { describe, expect, it } from "vitest";

import type { FilterCheck } from "../api/client";
import { checkVerdict, countText, labelText } from "./diagnostics";

const check = (changes: Partial<FilterCheck>): FilterCheck => ({
  part: 'env="prod"',
  field: "matcher",
  matched: 10,
  matched_without: 20,
  unit: "series",
  available: [],
  available_truncated: false,
  matching: [],
  ...changes,
});

describe("checkVerdict", () => {
  it("names what a filter part does to the selection", () => {
    expect(checkVerdict(check({}))).toBe("ok");
    expect(checkVerdict(check({ matched: 0 }))).toBe("excludes_all");
    expect(checkVerdict(check({ matched: 0, matched_without: 0 }))).toBe("matches_nothing");
    expect(checkVerdict(check({ matched: 0, matched_without: null }))).toBe("matches_nothing");
    expect(checkVerdict(check({ matched: 20 }))).toBe("no_effect");
    expect(checkVerdict(check({ matched: null }))).toBe("unknown");
  });

  it("flags a value the source does not have unless the list was cut", () => {
    const values = { available: ["production", "staging"] };

    expect(checkVerdict(check({ ...values, matched_without: null }))).toBe("no_such_value");
    expect(checkVerdict(check({ ...values, matched_without: null, available_truncated: true }))).toBe("ok");
    expect(checkVerdict(check({ ...values, matched_without: null, matching: ["production"] }))).toBe("ok");
  });
});

it("counts one of a unit in the singular", () => {
  expect([countText(1, "agents"), countText(2, "agents"), countText(1, "series"), countText(null, "rules")]).toEqual(["1 agent", "2 agents", "1 series", "—"]);
});

it("writes label sets like PromQL", () => {
  expect(labelText({ job: "node", instance: "a:9100" })).toBe('{job="node", instance="a:9100"}');
});
