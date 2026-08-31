import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useDisplayNames } from "@/hooks/useDisplayNames";
import type { CustomProviderInfo, ProviderInfo } from "@/types";

function provider(id: string, displayName: string, modelName: string): ProviderInfo {
  return {
    id,
    display_name: displayName,
    description: "",
    status: "error",
    media_types: ["video"],
    capabilities: [],
    configured_keys: [],
    missing_keys: [],
    models: { "m-1": { display_name: modelName, media_type: "video" } },
  } as unknown as ProviderInfo;
}

afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => {
    await i18n.changeLanguage("zh");
  });
});

function customProvider(id: number, displayName: string, modelName: string): CustomProviderInfo {
  return {
    id,
    display_name: displayName,
    discovery_format: "openai",
    base_url: "https://example.invalid",
    api_key_masked: "sk-***",
    // 停用的模型仍留在 /custom-providers 里，而候选与整页配置只列启用的。
    models: [
      {
        id: 1,
        model_id: "m-x",
        display_name: modelName,
        endpoint: "openai_chat",
        is_default: false,
        is_enabled: false,
      },
    ],
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
  } as unknown as CustomProviderInfo;
}

describe("useDisplayNames", () => {
  it("停用的自定义模型仍显示用户自填的名字，不退回裸 id", () => {
    const { result } = renderHook(() =>
      useDisplayNames([], [customProvider(3, "我的中转", "我的图像模型")], null, null),
    );

    expect(result.current.providerNames["custom-3"]).toBe("我的中转");
    expect(result.current.modelNames["custom-3/m-x"]).toBe("我的图像模型");
  });

  it("语言切换后重取目录译名，让已失去凭证的供应商也跟随语言", async () => {
    // 候选只列 ready 供应商，这一条只有目录层给得出名字。
    const getProviders = vi
      .spyOn(API, "getProviders")
      .mockResolvedValue({ providers: [provider("p", "P 英文名", "M 英文名")] });

    const { result } = renderHook(() =>
      useDisplayNames([provider("p", "P 中文名", "M 中文名")], [], null, null),
    );

    expect(result.current.providerNames.p).toBe("P 中文名");
    expect(result.current.modelNames["p/m-1"]).toBe("M 中文名");
    // 挂载时不重复请求：那一份就是调用方刚拉来的。
    expect(getProviders).not.toHaveBeenCalled();

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(result.current.providerNames.p).toBe("P 英文名"));
    expect(result.current.modelNames["p/m-1"]).toBe("M 英文名");
  });

  it("切回挂载语言时自取的快照出局，不把上一门语言的名字盖在调用方目录上", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue({
      providers: [provider("p", "P 英文名", "M 英文名")],
    });

    const { result } = renderHook(() =>
      useDisplayNames([provider("p", "P 中文名", "M 中文名")], [], null, null),
    );

    await act(async () => {
      await i18n.changeLanguage("en");
    });
    await waitFor(() => expect(result.current.providerNames.p).toBe("P 英文名"));

    await act(async () => {
      await i18n.changeLanguage("zh");
    });

    await waitFor(() => expect(result.current.providerNames.p).toBe("P 中文名"));
    expect(result.current.modelNames["p/m-1"]).toBe("M 中文名");
  });

  it("重取失败时保留上一份译名，不退回裸 id", async () => {
    vi.spyOn(API, "getProviders").mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() =>
      useDisplayNames([provider("p", "P 中文名", "M 中文名")], [], null, null),
    );

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(result.current.providerNames.p).toBe("P 中文名"));
    expect(result.current.modelNames["p/m-1"]).toBe("M 中文名");
  });
});
