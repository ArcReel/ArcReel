import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useTtsModelCapabilities } from "@/hooks/useTtsModelCapabilities";

afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => {
    await i18n.changeLanguage("zh");
  });
});

describe("useTtsModelCapabilities", () => {
  it("换语言即重取：音色标签由后端按 Accept-Language 成文", async () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockImplementation(async () => ({
      supports_speed: true,
      voices: [{ id: "Cherry", label: i18n.language === "en" ? "Cherry (sunny)" : "芊悦（阳光）" }],
    }));
    const { result } = renderHook(() => useTtsModelCapabilities("dashscope/qwen3-tts-flash"));

    await waitFor(() => expect(result.current?.voices[0].label).toBe("芊悦（阳光）"));

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(result.current?.voices[0].label).toBe("Cherry (sunny)"));
  });

  it("切到另一个模型后不沿用上一组音色：查询中为空，查到空表或查询失败也为空", async () => {
    let resolveNext: ((value: { supports_speed: boolean; voices: { id: string; label: string }[] }) => void) | null = null;
    vi.spyOn(API, "getTtsModelCapabilities").mockImplementation((backend: string) => {
      if (backend === "dashscope/qwen-audio-3.1-tts-plus") {
        return Promise.resolve({ supports_speed: true, voices: [{ id: "longanhuan_v3.1", label: "龙安欢" }] });
      }
      if (backend === "custom-1/qwen-audio-3.1-tts-next") {
        return new Promise((resolve) => {
          resolveNext = resolve;
        });
      }
      return Promise.reject(new Error("boom"));
    });
    const { result, rerender } = renderHook(({ backend }) => useTtsModelCapabilities(backend), {
      initialProps: { backend: "dashscope/qwen-audio-3.1-tts-plus" },
    });
    await waitFor(() => expect(result.current?.voices.map((v) => v.id)).toEqual(["longanhuan_v3.1"]));

    rerender({ backend: "custom-1/qwen-audio-3.1-tts-next" });
    expect(result.current).toBeNull();

    await act(async () => {
      resolveNext?.({ supports_speed: true, voices: [] });
    });
    expect(result.current).toEqual({ supportsSpeed: true, voices: [] });

    rerender({ backend: "custom-1/broken" });
    await act(async () => {});
    expect(result.current).toBeNull();
  });
});
