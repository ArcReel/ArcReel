import { describe, expect, it } from "vitest";
import { formatRelativeTime } from "./date-format";

const NOW = Date.parse("2026-09-17T12:00:00Z");

describe("formatRelativeTime", () => {
  it("picks the coarsest whole unit in the interface language", () => {
    expect(formatRelativeTime("2026-09-17T11:47:00Z", "zh", NOW)).toBe("13分钟前");
    expect(formatRelativeTime("2026-09-17T09:00:00Z", "en", NOW)).toBe("3 hours ago");
    expect(formatRelativeTime("2026-09-15T12:00:00Z", "en", NOW)).toBe("2 days ago");
  });

  it("treats timestamps without an offset as UTC", () => {
    expect(formatRelativeTime("2026-09-17T11:00:00", "en", NOW)).toBe("1 hour ago");
  });

  it("calls anything under a minute now", () => {
    expect(formatRelativeTime("2026-09-17T11:59:40Z", "en", NOW)).toBe("now");
  });

  it("returns null for missing or unparsable values", () => {
    expect(formatRelativeTime(null, "en", NOW)).toBeNull();
    expect(formatRelativeTime("not a time", "en", NOW)).toBeNull();
  });
});
