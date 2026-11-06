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
});
