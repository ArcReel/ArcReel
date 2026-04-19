import { describe, it, expect } from "vitest";
import { ASSET_COLORS, assetColor, type MentionKind } from "./asset-colors";

describe("asset-colors", () => {
  it("exposes three asset kinds plus 'unknown'", () => {
    const keys: MentionKind[] = ["character", "scene", "prop", "unknown"];
    for (const k of keys) {
      expect(ASSET_COLORS[k]).toBeDefined();
      expect(typeof ASSET_COLORS[k].textClass).toBe("string");
      expect(ASSET_COLORS[k].textClass.length).toBeGreaterThan(0);
    }
  });

  it("assetColor returns the matching palette", () => {
    expect(assetColor("character")).toBe(ASSET_COLORS.character);
    expect(assetColor("scene")).toBe(ASSET_COLORS.scene);
    expect(assetColor("prop")).toBe(ASSET_COLORS.prop);
  });

  it("assetColor falls back to 'unknown' for undefined", () => {
    expect(assetColor(undefined)).toBe(ASSET_COLORS.unknown);
  });
});
