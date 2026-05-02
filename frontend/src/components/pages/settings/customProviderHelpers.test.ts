import { describe, expect, it } from "vitest";
import type { ImageCap, MediaType } from "@/types";
import {
  priceLabel,
  urlPreviewFor,
  toggleDefaultReducer,
} from "./customProviderHelpers";

const id = (k: string) => k;

// 测试 fixture：模拟从 endpoint-catalog-store 派生的 endpoint→media map。
const ENDPOINT_TO_MEDIA: Record<string, MediaType> = {
  "openai-chat": "text",
  "gemini-generate": "text",
  "openai-images": "image",
  "openai-images-generations": "image",
  "openai-images-edits": "image",
  "gemini-image": "image",
  "openai-video": "video",
  "newapi-video": "video",
};

const ENDPOINT_TO_CAPS: Record<string, ImageCap[]> = {
  "openai-images": ["text_to_image", "image_to_image"],
  "openai-images-generations": ["text_to_image"],
  "openai-images-edits": ["image_to_image"],
  "gemini-image": ["text_to_image", "image_to_image"],
};

describe("priceLabel", () => {
  it("video endpoint → per-second label", () => {
    expect(priceLabel("newapi-video", ENDPOINT_TO_MEDIA, id).input).toBe("price_per_second");
    expect(priceLabel("openai-video", ENDPOINT_TO_MEDIA, id).output).toBe("");
  });
  it("image endpoint → per-image label", () => {
    expect(priceLabel("openai-images", ENDPOINT_TO_MEDIA, id).input).toBe("price_per_image");
    expect(priceLabel("gemini-image", ENDPOINT_TO_MEDIA, id).output).toBe("");
  });
  it("text endpoint → per-M-token labels", () => {
    expect(priceLabel("openai-chat", ENDPOINT_TO_MEDIA, id).input).toBe("price_per_m_input");
    expect(priceLabel("gemini-generate", ENDPOINT_TO_MEDIA, id).output).toBe("price_per_m_output");
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
      { key: "a", endpoint: "openai-chat", is_default: true },
      { key: "b", endpoint: "gemini-generate", is_default: false },
      { key: "c", endpoint: "openai-images", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "b", ENDPOINT_TO_MEDIA);
    expect(result.find((r) => r.key === "a")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "c")?.is_default).toBe(true);
  });

  it("toggling already-default row turns it off", () => {
    const rows = [{ key: "a", endpoint: "openai-chat", is_default: true }];
    expect(toggleDefaultReducer(rows, "a", ENDPOINT_TO_MEDIA)[0].is_default).toBe(false);
  });

  it("falls back to single-row toggle when catalog map is empty (catalog not loaded)", () => {
    // 回归：catalog 未加载时 endpointToMediaType={}，所有行 mediaType 都是 undefined。
    // 必须降级为单行 toggle，不能因 undefined === undefined 把不同媒体类型行当作同组互斥。
    const rows = [
      { key: "a", endpoint: "openai-chat", is_default: true },
      { key: "b", endpoint: "openai-images", is_default: true },
      { key: "c", endpoint: "newapi-video", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "b", {});
    expect(result.find((r) => r.key === "a")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "c")?.is_default).toBe(true);
  });

  it("falls back to single-row toggle when target endpoint is not in catalog", () => {
    const rows = [
      { key: "a", endpoint: "openai-chat", is_default: true },
      { key: "b", endpoint: "anthropic-messages", is_default: false },
    ];
    const result = toggleDefaultReducer(rows, "b", ENDPOINT_TO_MEDIA);
    expect(result.find((r) => r.key === "a")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
  });

  it("split image endpoints with disjoint caps coexist as defaults", () => {
    const rows = [
      { key: "g", endpoint: "openai-images-generations", is_default: false },
      { key: "e", endpoint: "openai-images-edits", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "g", ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    // -generations 设为 default，不应清掉 -edits（capability 不交叠）
    expect(result.find((r) => r.key === "g")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "e")?.is_default).toBe(true);
  });

  it("wildcard openai-images clears split image rows when set as default", () => {
    const rows = [
      { key: "w", endpoint: "openai-images", is_default: false },
      { key: "g", endpoint: "openai-images-generations", is_default: true },
      { key: "e", endpoint: "openai-images-edits", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "w", ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(result.find((r) => r.key === "w")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "g")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "e")?.is_default).toBe(false);
  });

  it("two -generations defaults are mutually exclusive (same capability slot)", () => {
    const rows = [
      { key: "g1", endpoint: "openai-images-generations", is_default: true },
      { key: "g2", endpoint: "openai-images-generations", is_default: false },
    ];
    const result = toggleDefaultReducer(rows, "g2", ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(result.find((r) => r.key === "g1")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "g2")?.is_default).toBe(true);
  });
});
