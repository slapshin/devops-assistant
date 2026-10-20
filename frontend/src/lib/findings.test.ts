import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Finding } from "../api/client";
import { dayPartText, daySpans } from "./findings";
import { timezone } from "./format";

const episode = (start: string, end: string) => ({ start, end }) as Finding;

beforeEach(() => {
  timezone.value = "utc";
});

afterEach(() => {
  timezone.value = "local";
});

describe("daySpans", () => {
  it("places an episode on the 24-hour axis", () => {
    expect(daySpans(episode("2026-10-19T10:30:00Z", "2026-10-19T12:00:00Z"))).toEqual([[10.5, 12]]);
  });

  it("wraps an episode over midnight", () => {
    expect(daySpans(episode("2026-10-19T22:00:00Z", "2026-10-20T02:00:00Z"))).toEqual([[22, 24], [0, 2]]);
  });

  it("fills the whole day for an episode of a day or more", () => {
    expect(daySpans(episode("2026-10-19T10:00:00Z", "2026-10-20T11:00:00Z"))).toEqual([[0, 24]]);
  });
});

describe("dayPartText", () => {
  it("names the part of the day an episode falls in", () => {
    expect(dayPartText(episode("2026-10-19T03:00:00Z", "2026-10-19T04:00:00Z"))).toBe("Night");
    expect(dayPartText(episode("2026-10-19T09:00:00Z", "2026-10-19T09:05:00Z"))).toBe("Morning");
    expect(dayPartText(episode("2026-10-19T14:55:00Z", "2026-10-19T15:30:00Z"))).toBe("Afternoon");
    expect(dayPartText(episode("2026-10-19T20:25:00Z", "2026-10-20T00:00:00Z"))).toBe("Evening");
  });

  it("names the first and last part of an episode that spans several", () => {
    expect(dayPartText(episode("2026-10-19T10:50:00Z", "2026-10-20T02:30:00Z"))).toBe("Morning → Night");
  });

  it("says all day for an episode of a day or more", () => {
    expect(dayPartText(episode("2026-10-19T10:00:00Z", "2026-10-20T10:00:00Z"))).toBe("All day");
  });

  it("follows the chosen time zone", () => {
    timezone.value = "local";
    const start = new Date(2026, 9, 19, 19, 0);
    const end = new Date(2026, 9, 19, 20, 0);

    expect(dayPartText(episode(start.toISOString(), end.toISOString()))).toBe("Evening");
  });
});
