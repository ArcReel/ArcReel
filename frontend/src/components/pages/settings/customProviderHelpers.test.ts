import { describe, expect, it } from "vitest";
import type { CapabilityOverrides, ImageCap, MediaType } from "@/types";
import {
  urlPreviewFor,
  toggleDefaultReducer,
  mergeDiscoveredModels,
  withLastFrameOverride,
  withDurationsOverride,
} from "./customProviderHelpers";

// Test fixture simulating endpoint→media maps derived from endpoint-catalog-store.
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

  it("treats two image endpoints that declare no capabilities as colliding", () => {
    // ComfyUI endpoint capabilities are empty before inference exists; two defaults cannot distinguish slots, so servers reject them.
    // Divergent predicates would permit unsaveable configurations in UI.
    const rows = [
      { key: "a", endpoint: "ce-1", is_default: true },
      { key: "b", endpoint: "ce-2", is_default: false },
    ];
    const media: Record<string, MediaType> = { "ce-1": "image", "ce-2": "image" };

    const result = toggleDefaultReducer(rows, "b", media, { "ce-1": [], "ce-2": [] });

    expect(result.find((r) => r.key === "a")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
  });

  it("toggling already-default row turns it off", () => {
    const rows = [{ key: "a", endpoint: "openai-chat", is_default: true }];
    expect(toggleDefaultReducer(rows, "a", ENDPOINT_TO_MEDIA)[0].is_default).toBe(false);
  });

  it("falls back to single-row toggle when catalog map is empty (catalog not loaded)", () => {
    // Regression: unloaded catalogs mean endpointToMediaType={}, all rows have undefined mediaType.
    // Degrade to single-row toggles, never treat different-media rows as mutually exclusive through undefined === undefined.
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
      { key: "b", endpoint: "acme-unknown", is_default: false },
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
    // -generations defaults must not clear -edits; capabilities do not overlap.
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
    // Regression: disabling wildcard image defaults must not clear default split-edits too
    // (wildcard/-edits overlap I2I, but users enabled no new defaults, so no exclusion cleanup).
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
    // Existing text default z-chat; upstream adds earlier-sorting a-chat, marked default by discovery.
    // Naive merging makes two text defaults, rejected as default_model_conflict; resolution preserves existing defaults only.
    const prev = [row("z-chat", "openai-chat", true)];
    const discovered = [row("a-chat", "openai-chat", true), row("z-chat", "openai-chat", false)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["z-chat"]);
  });

  it("lets a discovered default fill an empty media slot", () => {
    // Existing text defaults only; new discovered videos fill vacant video defaults.
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
    // Existing -edits I2I default plus discovered -generations T2I default: no overlap, retain both.
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
    // Existing -edits I2I default plus wildcard T2I+I2I default: occupied I2I makes wildcard yield.
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
    // Unloaded catalogs mean unknown endpoint media; skip resolution, matching toggleDefaultReducer degradation.
    const prev = [row("z-chat", "openai-chat", true)];
    const discovered = [row("a-chat", "openai-chat", true), row("z-chat", "openai-chat", false)];
    const merged = mergeDiscoveredModels(prev, discovered, {});
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id).sort()).toEqual([
      "a-chat",
      "z-chat",
    ]);
  });

  it("keeps multiple manually-added rows with empty model_id (no Map key collision)", () => {
    // Users manually add two empty model_id rows (both ""), then fetch models.
    // model_id Map deduplication collides empty keys, silently losing rows; fixes preserve both.
    const prev = [
      row("", "openai-chat", false),
      row("", "newapi-video", false),
      row("z-chat", "openai-chat", true),
    ];
    const discovered = [row("z-chat", "openai-chat", false), row("a-chat", "openai-chat", true)];
    const merged = mergeDiscoveredModels(prev, discovered, ENDPOINT_TO_MEDIA);
    expect(merged.filter((m) => m.model_id === "").length).toBe(2);
    // Existing defaults remain protected; discovered same-media_type defaults yield.
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["z-chat"]);
  });

  it("create-mode passthrough does not reconcile discovery defaults", () => {
    // Create mode passes discovery responses unchanged, without slot resolution. Real discovery emits at most one default
    // per media_type; artificial overlap locks unchanged-discovery contracts, leaving conflicts for backend validation.
    const discovered = [row("w", "openai-images", true), row("e", "openai-images-edits", true)];
    const merged = mergeDiscoveredModels([], discovered, ENDPOINT_TO_MEDIA, ENDPOINT_TO_CAPS);
    expect(merged.filter((m) => m.is_default).map((m) => m.model_id)).toEqual(["w", "e"]);
  });
});

describe("withLastFrameOverride", () => {
  it("跟随（undefined）从字典移除该键，而不是写成 false", () => {
    expect(withLastFrameOverride({ last_frame: true }, undefined)).toBeNull();
    expect(withLastFrameOverride({ last_frame: false }, undefined)).toBeNull();
  });

  it("强制开/关写入显式布尔值", () => {
    expect(withLastFrameOverride(null, true)).toEqual({ last_frame: true });
    expect(withLastFrameOverride(null, false)).toEqual({ last_frame: false });
  });

  it("保留字典里其他维度的覆盖，移除 last_frame 后不清空整个字典", () => {
    const prev = { last_frame: true, first_frame: false } as CapabilityOverrides;
    expect(withLastFrameOverride(prev, undefined)).toEqual({ first_frame: false });
    expect(withLastFrameOverride(prev, false)).toEqual({ last_frame: false, first_frame: false });
  });

  it("不改动入参字典", () => {
    const prev: CapabilityOverrides = { last_frame: true };
    withLastFrameOverride(prev, undefined);
    expect(prev).toEqual({ last_frame: true });
  });
});

describe("withDurationsOverride", () => {
  it("写入档位时保留其余维度的覆盖", () => {
    const prev = { last_frame: false } as CapabilityOverrides;
    expect(withDurationsOverride(prev, [4, 8])).toEqual({ last_frame: false, durations: [4, 8] });
  });

  it("移除（undefined）后字典为空即收敛回 null，回到跟随 workflow 原生时长", () => {
    expect(withDurationsOverride({ durations: [4, 8] }, undefined)).toBeNull();
    expect(withDurationsOverride(null, undefined)).toBeNull();
  });
});
