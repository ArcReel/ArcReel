import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceSampleButton } from "./VoiceSampleButton";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useTasksStore } from "@/stores/tasks-store";

function setAudioConfigured(configured: boolean) {
  useConfigStatusStore.setState({
    availableMediaTypes: configured ? ["image", "video", "text", "audio"] : ["image", "video", "text"],
  });
}

describe("VoiceSampleButton", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useConfigStatusStore.setState({ availableMediaTypes: [] });
  });

  it("disables the entry and explains why when no audio provider is configured", () => {
    setAudioConfigured(false);
    const spy = vi.spyOn(API, "getAudioBackendVoices");
    render(
      <VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />,
    );

    const button = screen.getByRole("button", { name: /配置音频供应商/ });
    expect(button).toBeDisabled();

    // 入口禁用即不打开弹窗，也不为每张角色卡预取音色列表
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("opens the modal and loads voices when an audio provider is configured", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
    });

    render(
      <VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "芊悦 · 阳光正向的自然年轻女声" })).toBeInTheDocument();
    });
    // 默认文案按界面语言预填，且可编辑
    expect(screen.getByDisplayValue(/这是一段声音示例/)).toBeInTheDocument();
  });

  it("stays disabled while the character is busy with another task", () => {
    setAudioConfigured(true);
    render(
      <VoiceSampleButton projectName="demo" characterName="艾莉" busy onSaved={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /用 TTS 生成参考音频/ })).toBeDisabled();
  });
});
