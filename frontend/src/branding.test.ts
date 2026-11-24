import { afterEach, describe, expect, it, vi } from "vitest";
import { getDocsUrl } from "@/branding";

describe("branding docs URL", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("defaults BRAND.docsUrl to https://docs.arc-reel.com", async () => {
    vi.stubEnv("VITE_BRAND_DOCS_URL", undefined);
    vi.resetModules();
    const { BRAND } = await import("@/branding");
    expect(BRAND.docsUrl).toBe("https://docs.arc-reel.com");
  });

  it.each(["zh", "zh-CN", "zh-Hans", "ZH-tw", "zh_CN"])(
    "resolves Chinese locale %s to the root docs URL",
    (language) => {
      expect(getDocsUrl(language, "https://docs.example.com")).toBe("https://docs.example.com/");
    },
  );

  it.each(["en", "en-US", "vi", "vi-VN", "ru", "ru-RU", "", undefined])(
    "resolves non-Chinese locale %s to the English docs path",
    (language) => {
      expect(getDocsUrl(language, "https://docs.example.com")).toBe("https://docs.example.com/en/");
    },
  );

  it("normalizes trailing slashes and falls back on blank base URLs", () => {
    expect(getDocsUrl("zh", "https://docs.example.com///")).toBe("https://docs.example.com/");
    expect(getDocsUrl("en", "https://docs.example.com/")).toBe("https://docs.example.com/en/");
    expect(getDocsUrl("zh", "   ")).toBe("https://docs.arc-reel.com/");
    expect(getDocsUrl("en", "///")).toBe("https://docs.arc-reel.com/en/");
  });

  it("supports VITE_BRAND_DOCS_URL override and falls back when empty", async () => {
    vi.stubEnv("VITE_BRAND_DOCS_URL", "https://custom.docs.example/");
    vi.resetModules();
    const customMod = await import("@/branding");
    expect(customMod.BRAND.docsUrl).toBe("https://custom.docs.example");
    expect(customMod.getDocsUrl("zh")).toBe("https://custom.docs.example/");
    expect(customMod.getDocsUrl("en")).toBe("https://custom.docs.example/en/");

    vi.stubEnv("VITE_BRAND_DOCS_URL", "   ");
    vi.resetModules();
    const fallbackMod = await import("@/branding");
    expect(fallbackMod.BRAND.docsUrl).toBe("https://docs.arc-reel.com");
  });
});
