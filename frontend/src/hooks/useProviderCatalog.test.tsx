import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useProviderCatalog } from "@/hooks/useProviderCatalog";
import type { CatalogRefreshResult } from "@/hooks/useProviderCatalog";
import { createDeferred } from "@/test/deferred";
import type { CustomProviderInfo, ProviderInfo } from "@/types";

function preset(id: string): ProviderInfo {
  return {
    id,
    display_name: id,
    description: "",
    status: "ready",
    media_types: ["video"],
    capabilities: [],
    configured_keys: [],
    missing_keys: [],
    models: {},
  };
}

function custom(id: number): CustomProviderInfo {
  return {
    id,
    display_name: `custom-${id}`,
    discovery_format: "openai",
    base_url: "https://example.invalid",
    api_key_masked: "sk-***",
    models: [],
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useProviderCatalog", () => {
  it("resolves refresh with the latest custom providers", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [preset("a")] });
    let saved = false;
    vi.spyOn(API, "listCustomProviders").mockImplementation(() =>
      Promise.resolve({ providers: saved ? [custom(1), custom(2)] : [custom(1)] }),
    );

    const { result } = renderHook(() => useProviderCatalog("zh"));
    await waitFor(() => expect(result.current.customProviders.map((p) => p.id)).toEqual([1]));
    saved = true;

    let returned: CatalogRefreshResult = { status: "aborted" };
    await act(async () => {
      returned = await result.current.refresh();
    });

    expect(returned).toEqual({ status: "ok", customProviders: [custom(1), custom(2)] });
    expect(result.current.customProviders.map((p) => p.id)).toEqual([1, 2]);
  });

  it("does not write back a read that a later one took over", async () => {
    // 首拉与保存后的重取共用一个取消域：慢的那次回来时写下的是过期目录，必须被丢弃。
    const stale = createDeferred<{ providers: ProviderInfo[] }>();
    let call = 0;
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      call += 1;
      return call === 1 ? stale.promise : Promise.resolve({ providers: [preset("fresh")] });
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    const { result } = renderHook(() => useProviderCatalog("zh"));

    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.providers.map((p) => p.id)).toEqual(["fresh"]);

    await act(async () => {
      stale.resolve({ providers: [preset("stale")] });
      await Promise.resolve();
    });

    expect(result.current.providers.map((p) => p.id)).toEqual(["fresh"]);
  });

  it("keeps the previous catalog when a silent language refetch fails", async () => {
    let failing = false;
    vi.spyOn(API, "getProviders").mockImplementation(() =>
      failing ? Promise.reject(new Error("network down")) : Promise.resolve({ providers: [preset("a")] }),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    const { result, rerender } = renderHook(({ lang }) => useProviderCatalog(lang), {
      initialProps: { lang: "zh" },
    });
    await waitFor(() => expect(result.current.providers.map((p) => p.id)).toEqual(["a"]));

    failing = true;
    await act(async () => {
      rerender({ lang: "en" });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.providers.map((p) => p.id)).toEqual(["a"]);
  });
});
