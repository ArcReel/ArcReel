import { describe, expect, it } from "vitest";
import type { ImageCap, MediaType } from "@/types";
import {
  priceLabel,
  urlPreviewFor,
  toggleDefaultReducer,
  mergeDiscoveredModels,
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
  "openai-tts": "audio",
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
  it("audio endpoint → per-10k-characters label", () => {
    expect(priceLabel("openai-tts", ENDPOINT_TO_MEDIA, id).input).toBe("price_per_10k_chars");
    expect(priceLabel("openai-tts", ENDPOINT_TO_MEDIA, id).output).toBe("");
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

  it("disabling an existing default does NOT clear other capability-overlapping defaults", () => {
    // 回归：取消 wildcard image 默认时，不应把同样作为默认的 split-edits 也清掉
    // （wildcard 与 -edits 在 I2I 槽 overlap，但用户并未启用新默认，不该触发互斥清理）
    const rows = [
      { key: "w", endpoint: "openai-images", is_default: true },
      { key: "e", endpoint: "openai-images-edits", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "w", ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(result.find((r) => r.key === "w")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "e")?.is_default).toBe(true);
  });

  it("disabling an existing text default leaves other text defaults untouched", () => {
    const rows = [
      { key: "a", endpoint: "openai-chat", is_default: true },
      { key: "b", endpoint: "gemini-generate", is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "a", ENDPOINT_TO_MEDIA);
    expect(result.find((r) => r.key === "a")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
  });
});

describe("mergeDiscoveredModels", () => {
  const row = (model_id: string, endpoint: string, is_default: boolean) => ({
    model_id,
    endpoint,
    is_default,
  });

  it("keeps existing default and drops discovered default in same media_type (regression)", () => {
    // 既存 provider：文本默认 = z-chat。上游新增排序更靠前的 a-chat，发现接口把首项 a-chat 标默认。
    // 朴素合并会得到两个 text 默认 → 保存被后端 default_model_conflict 拒绝。消解后只保留既存默认。
    const prev = [row("z-chat", "openai-chat", true)];
    const discovered = [row("a-chat", "openai-chat", true), row("z-chat", "openai-chat", false)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["z-chat"]);
  });

  it("lets a discovered default fill an empty media slot", () => {
    // 既存仅文本默认；发现新增视频模型 → 视频槽空 → 允许补默认
    const prev = [row("z-chat", "openai-chat", true)];
    const discovered = [row("z-chat", "openai-chat", false), row("v1", "newapi-video", true)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.find((m) => m.model_id === "z-chat")?.is_default).toBe(true);
    expect(merged.find((m) => m.model_id === "v1")?.is_default).toBe(true);
  });

  it("preserves manually-added models absent from the discovery response", () => {
    const prev = [row("manual", "openai-chat", false), row("z-chat", "openai-chat", true)];
    const discovered = [row("z-chat", "openai-chat", false)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.map((m) => m.model_id).sort()).toEqual(["manual", "z-chat"]);
    expect(merged.find((m) => m.model_id === "z-chat")?.is_default).toBe(true);
  });

  it("keeps disjoint-capability image defaults (split generations vs edits)", () => {
    // 既存 -edits(I2I) 默认；发现 -generations(T2I) 标默认 → capability 不交叠 → 两者都保留
    const prev = [row("e", "openai-images-edits", true)];
    const discovered = [
      row("g", "openai-images-generations", true),
      row("e", "openai-images-edits", false),
    ];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(merged.find((m) => m.model_id === "g")?.is_default).toBe(true);
    expect(merged.find((m) => m.model_id === "e")?.is_default).toBe(true);
  });

  it("makes a discovered wildcard-image default yield to an overlapping existing default", () => {
    // 既存 -edits(I2I) 默认；发现 wildcard image(T2I+I2I) 标默认 → I2I 槽已占 → wildcard 让出
    const prev = [row("e", "openai-images-edits", true)];
    const discovered = [row("w", "openai-images", true), row("e", "openai-images-edits", false)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(merged.find((m) => m.model_id === "e")?.is_default).toBe(true);
    expect(merged.find((m) => m.model_id === "w")?.is_default).toBe(false);
  });

  it("leaves create-mode (empty prev) discovery defaults intact", () => {
    const discovered = [
      row("a-chat", "openai-chat", true),
      row("b-chat", "openai-chat", false),
      row("v1", "newapi-video", true),
    ];
    const merged = mergeDiscoveredModels([], discovered, ENDPOINT_TO_MEDIA);
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["a-chat", "v1"]);
  });

  it("does not reconcile when catalog is unloaded (unknown endpoints never claim slots)", () => {
    // catalog 未加载 → 所有 endpoint media 未知 → 不消解（与 toggleDefaultReducer 的降级一致）
    const prev = [row("z-chat", "openai-chat", true)];
    const discovered = [row("a-chat", "openai-chat", true), row("z-chat", "openai-chat", false)];
    const merged = mergeDiscoveredModels(prev, discovered, {});
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id).sort()).toEqual([
      "a-chat",
      "z-chat",
    ]);
  });

  it("keeps multiple manually-added rows with empty model_id (no Map key collision)", () => {
    // 用户连点「手动添加」加了两行未填 model_id（都为 ""），再点获取模型。
    // 若用 model_id 建 Map 去重，两空行键冲突会静默丢一行；修复后两行都应保留。
    const prev = [
      row("", "openai-chat", false),
      row("", "newapi-video", false),
      row("z-chat", "openai-chat", true),
    ];
    const discovered = [row("z-chat", "openai-chat", false), row("a-chat", "openai-chat", true)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.filter((m) => m.model_id === "").length).toBe(2);
    // 既存默认仍受保护，发现的同 media_type 默认让出
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["z-chat"]);
  });

  it("create-mode passthrough does not reconcile discovery defaults", () => {
    // create 模式原样透传 discovery 响应、不做槽位消解：实际 discovery 每个 media_type 至多
    // 一个默认（不会冲突），此处用人造的重叠默认锁定「不改写 discovery」契约，冲突交后端校验。
    const discovered = [row("w", "openai-images", true), row("e", "openai-images-edits", true)];
    const merged = mergeDiscoveredModels([], discovered, ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["w", "e"]);
  });
});
