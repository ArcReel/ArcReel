import { describe, it, expect } from "vitest";
import { gridStoryboardEnabled, normalizeRoute, type GenerationRoute } from "./generation-mode";

describe("normalizeRoute", () => {
  it("keeps both routes", () => {
    for (const r of ["storyboard", "reference_video"] as GenerationRoute[]) {
      expect(normalizeRoute(r)).toBe(r);
    }
  });
  it("falls back to 'storyboard' when the field is absent or unparseable", () => {
    expect(normalizeRoute(undefined)).toBe("storyboard");
    expect(normalizeRoute(null)).toBe("storyboard");
    expect(normalizeRoute(42)).toBe("storyboard");
  });
});

describe("gridStoryboardEnabled", () => {
  it("requires the storyboard route with the toggle on", () => {
    expect(gridStoryboardEnabled({ generation_mode: "storyboard", grid_storyboard: true })).toBe(true);
    expect(gridStoryboardEnabled({ generation_mode: "storyboard", grid_storyboard: false })).toBe(false);
    expect(gridStoryboardEnabled({ generation_mode: "storyboard" })).toBe(false);
  });
  it("stays off on the reference route even with a leftover toggle", () => {
    expect(gridStoryboardEnabled({ generation_mode: "reference_video", grid_storyboard: true })).toBe(false);
  });
  it("handles missing project data", () => {
    expect(gridStoryboardEnabled(null)).toBe(false);
    expect(gridStoryboardEnabled(undefined)).toBe(false);
  });
});
