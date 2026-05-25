import { describe, expect, it } from "vitest";
import { getProjectDisplayName } from "./project-display";

describe("getProjectDisplayName", () => {
  const UNTITLED = "未命名项目";

  it("falls back when title is null / undefined / empty / whitespace", () => {
    expect(getProjectDisplayName(undefined, UNTITLED)).toBe(UNTITLED);
    expect(getProjectDisplayName(null, UNTITLED)).toBe(UNTITLED);
    expect(getProjectDisplayName("", UNTITLED)).toBe(UNTITLED);
    expect(getProjectDisplayName("   ", UNTITLED)).toBe(UNTITLED);
    expect(getProjectDisplayName("\t\n", UNTITLED)).toBe(UNTITLED);
  });

  it("returns trimmed title when non-empty", () => {
    expect(getProjectDisplayName("第一集", UNTITLED)).toBe("第一集");
    expect(getProjectDisplayName("  第一集  ", UNTITLED)).toBe("第一集");
    expect(getProjectDisplayName("My Novel", UNTITLED)).toBe("My Novel");
  });

  it("respects caller-provided untitled label across locales", () => {
    expect(getProjectDisplayName(undefined, "Untitled Project")).toBe("Untitled Project");
    expect(getProjectDisplayName("", "Dự án chưa đặt tên")).toBe("Dự án chưa đặt tên");
  });
});
