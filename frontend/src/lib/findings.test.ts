import { describe, expect, it } from "vitest";

import type { Finding } from "../api/client";
import { agoRangeText, windowSpan } from "./findings";

const episode = (start: string, end: string) => ({ start, end }) as Finding;

const END = "2026-10-21T12:00:00Z";

describe("windowSpan", () => {
  it("places an episode on the analysed day, ending at the report's end time", () => {
    expect(windowSpan(episode("2026-10-20T18:00:00Z", "2026-10-21T06:30:00Z"), END)).toEqual([6, 18.5]);
  });

  it("clamps an episode to the analysed day", () => {
    expect(windowSpan(episode("2026-10-20T10:00:00Z", END), END)).toEqual([0, 24]);
  });
});

describe("agoRangeText", () => {
  it("says how long before the report's end an episode started and ended", () => {
    expect(agoRangeText(episode("2026-10-20T16:55:00Z", "2026-10-21T08:00:00Z"), END)).toBe("19 h → 4 h ago");
  });

  it("ends at now for an episode still running at the report's end", () => {
    expect(agoRangeText(episode("2026-10-21T09:00:00Z", END), END)).toBe("3 h ago → now");
  });

  it("counts minutes within the last hour", () => {
    expect(agoRangeText(episode("2026-10-21T11:15:00Z", "2026-10-21T11:40:00Z"), END)).toBe("45 min → 20 min ago");
  });

  it("names one time for an episode shorter than the rounding", () => {
    expect(agoRangeText(episode("2026-10-21T02:10:00Z", "2026-10-21T02:20:00Z"), END)).toBe("10 h ago");
  });
});
