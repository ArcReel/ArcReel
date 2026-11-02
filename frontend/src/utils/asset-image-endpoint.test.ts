import { describe, expect, it } from "vitest";

import { ASSET_IMAGE_SLOT_KEY, resolveAssetImageEndpoint } from "./asset-image-endpoint";

describe("ASSET_IMAGE_SLOT_KEY", () => {
  it("maps each asset type to its project-layer default endpoint slot key", () => {
    expect(ASSET_IMAGE_SLOT_KEY).toEqual({
      character: "default_image_backend_character",
      character_derivative: "default_image_backend_character_derivative",
      scene: "default_image_backend_scene",
      prop: "default_image_backend_prop",
      product: "default_image_backend_product",
    });
  });
});

describe("resolveAssetImageEndpoint", () => {
  const base = { assetType: "scene" as const, useImageToImage: false, project: null, globalSettings: null };

  it("falls through to auto when nothing is configured", () => {
    expect(resolveAssetImageEndpoint(base)).toEqual({ value: "", source: "auto" });
  });

  it("prefers the project asset slot over every other layer", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        project: {
          default_image_backend_scene: "vertex/scene-model",
          image_provider_t2i: "openai/image-model",
          default_image_backend: "anthropic/default",
        },
        globalSettings: { default_image_backend_scene: "global/scene" },
      }),
    ).toEqual({ value: "vertex/scene-model", source: "project_slot" });
  });

  it("uses the project t2i bucket when no asset slot is set", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        project: { image_provider_t2i: "openai/image-model", default_image_backend: "anthropic/default" },
      }),
    ).toEqual({ value: "openai/image-model", source: "project_bucket" });
  });

  it("uses the i2i bucket key for image-to-image sheets", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        useImageToImage: true,
        project: { image_provider_t2i: "openai/t2i", image_provider_i2i: "openai/i2i" },
      }),
    ).toEqual({ value: "openai/i2i", source: "project_bucket" });
  });

  it("uses the project default backend when no slot or bucket is set", () => {
    expect(
      resolveAssetImageEndpoint({ ...base, project: { default_image_backend: "anthropic/default" } }),
    ).toEqual({ value: "anthropic/default", source: "project_default" });
  });

  it("prefers the global asset slot over the global bucket and default", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        globalSettings: {
          default_image_backend_scene: "global/scene",
          default_image_backend_t2i: "global/t2i",
          default_image_backend: "global/default",
        },
      }),
    ).toEqual({ value: "global/scene", source: "global_slot" });
  });

  it("uses the global t2i bucket when no global slot is set", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        globalSettings: { default_image_backend_t2i: "global/t2i", default_image_backend: "global/default" },
      }),
    ).toEqual({ value: "global/t2i", source: "global_bucket" });
  });

  it("uses the global default backend as the last configured layer", () => {
    expect(
      resolveAssetImageEndpoint({ ...base, globalSettings: { default_image_backend: "global/default" } }),
    ).toEqual({ value: "global/default", source: "global_default" });
  });

  it("skips blank or whitespace-only values and continues the cascade", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        project: {
          default_image_backend_scene: "   ",
          image_provider_t2i: "",
          default_image_backend: "anthropic/default",
        },
      }),
    ).toEqual({ value: "anthropic/default", source: "project_default" });
  });

  it("trims the winning value", () => {
    expect(
      resolveAssetImageEndpoint({ ...base, globalSettings: { default_image_backend: "  global/default  " } }),
    ).toEqual({ value: "global/default", source: "global_default" });
  });

  it("never lets a global layer shadow a project layer", () => {
    expect(
      resolveAssetImageEndpoint({
        ...base,
        project: { default_image_backend: "project/default" },
        globalSettings: { default_image_backend_scene: "global/scene" },
      }),
    ).toEqual({ value: "project/default", source: "project_default" });
  });

  it("resolves character derivatives against their own slot, distinct from the main character sheet", () => {
    const derivative = {
      assetType: "character_derivative" as const,
      useImageToImage: true,
      project: {
        default_image_backend_character: "vertex/main-sheet",
        default_image_backend_character_derivative: "openai/derivative",
      },
      globalSettings: { default_image_backend_character: "global/main-sheet" },
    };
    expect(resolveAssetImageEndpoint(derivative)).toEqual({
      value: "openai/derivative",
      source: "project_slot",
    });
    // 没配衍生槽位时落回本体槽位之外的 i2i 桶 / 默认，而不是本体的 character 槽位
    expect(resolveAssetImageEndpoint({ ...derivative, project: { image_provider_i2i: "openai/i2i" } })).toEqual({
      value: "openai/i2i",
      source: "project_bucket",
    });
  });
});
