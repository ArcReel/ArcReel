import { describe, expect, it } from "vitest";
import {
  priceLabel,
  urlPreviewFor,
  toggleDefaultReducer,
} from "./customProviderHelpers";

const id = (k: string) => k;

describe("priceLabel", () => {
  it("video endpoint → per-second label", () => {
    expect(priceLabel("newapi-video", id).input).toBe("price_per_second");
    expect(priceLabel("openai-video", id).output).toBe("");
  });
  it("image endpoint → per-image label", () => {
    expect(priceLabel("openai-images", id).input).toBe("price_per_image");
    expect(priceLabel("gemini-image", id).output).toBe("");
  });
  it("text endpoint → per-M-token labels", () => {
    expect(priceLabel("openai-chat", id).input).toBe("price_per_m_input");
    expect(priceLabel("gemini-generate", id).output).toBe("price_per_m_output");
  });
});

describe("urlPreviewFor", () => {
  it("openai appends /v1 when missing", () => {
    expect(urlPreviewFor("openai", "https://api.example.com")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("openai preserves /v1", () => {
    expect(urlPreviewFor("openai", "https://api.example.com/v1")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("openai strips trailing slash and appends /v1", () => {
    expect(urlPreviewFor("openai", "https://api.example.com/")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("google uses /v1beta/models", () => {
    expect(urlPreviewFor("google", "https://generativelanguage.googleapis.com")).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models",
    );
  });
  it("google strips user-supplied version path", () => {
    expect(urlPreviewFor("google", "https://generativelanguage.googleapis.com/v1beta")).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models",
    );
  });
  it("empty base_url returns null", () => {
    expect(urlPreviewFor("openai", "")).toBeNull();
    expect(urlPreviewFor("google", "  ")).toBeNull();
  });
});

describe("toggleDefaultReducer", () => {
  it("toggles target row and clears siblings within same media_type", () => {
    const rows = [
      { key: "a", endpoint: "openai-chat" as const, is_default: true },
      { key: "b", endpoint: "gemini-generate" as const, is_default: false },
      { key: "c", endpoint: "openai-images" as const, is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "b");
    expect(result.find((r) => r.key === "a")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "c")?.is_default).toBe(true);
  });

  it("toggling already-default row turns it off", () => {
    const rows = [{ key: "a", endpoint: "openai-chat" as const, is_default: true }];
    expect(toggleDefaultReducer(rows, "a")[0].is_default).toBe(false);
  });
});
