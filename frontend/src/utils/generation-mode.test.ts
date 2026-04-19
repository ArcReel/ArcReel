import { describe, it, expect } from "vitest";
import { effectiveMode, normalizeMode, type GenerationMode } from "./generation-mode";

describe("normalizeMode", () => {
  it("maps legacy 'single' to 'storyboard'", () => {
    expect(normalizeMode("single")).toBe("storyboard");
  });
  it("keeps canonical values", () => {
    for (const m of ["storyboard", "grid", "reference_video"] as GenerationMode[]) {
      expect(normalizeMode(m)).toBe(m);
    }
  });
  it("returns 'storyboard' for undefined/null/unknown", () => {
    expect(normalizeMode(undefined)).toBe("storyboard");
    expect(normalizeMode(null)).toBe("storyboard");
    expect(normalizeMode("weird")).toBe("storyboard");
  });
});

describe("effectiveMode", () => {
  it("prefers episode.generation_mode over project.generation_mode", () => {
    expect(effectiveMode({ generation_mode: "grid" }, { generation_mode: "reference_video" }))
      .toBe("reference_video");
  });
  it("falls back to project mode if episode has none", () => {
    expect(effectiveMode({ generation_mode: "reference_video" }, {})).toBe("reference_video");
  });
  it("falls back to 'storyboard' when both missing", () => {
    expect(effectiveMode({}, {})).toBe("storyboard");
  });
  it("normalizes legacy 'single' on both levels", () => {
    expect(effectiveMode({ generation_mode: "single" }, {})).toBe("storyboard");
    expect(effectiveMode({}, { generation_mode: "single" })).toBe("storyboard");
  });
});
