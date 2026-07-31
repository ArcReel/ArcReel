import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceSampleButton } from "./VoiceSampleButton";
import { API } from "@/api";
import * as generationActions from "@/actions/generation";
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

  it("resets the stuck pause icon when regenerating after playing a preview", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockResolvedValue({
      taskIds: ["task-1"],
      deduped: false,
    });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "芊悦 · 阳光正向的自然年轻女声" })).toBeInTheDocument();
    });

    // 点击「生成」提交样本 A，taskId 落定为 task-1。
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    // 样本 A 成功，播放它——isPreviewPlaying 置 true。
    useTasksStore.setState({
      tasks: [
        {
          task_id: "task-1",
          project_name: "demo",
          task_type: "voice_sample",
          resource_id: "艾莉",
          status: "succeeded",
          result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
          updated_at: "2026-07-31T00:00:00Z",
        } as never,
      ],
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
    });
    fireEvent.play(document.querySelector("audio") as HTMLAudioElement);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
    });

    // 点击「重新生成」：taskId 清空、旧 <audio> 卸载，isPreviewPlaying 须同步复位，
    // 否则新样本挂载后仍显示「暂停」，用户无法播放它。
    fireEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "暂停" })).not.toBeInTheDocument();
    });
  });

  it("resets the stuck pause icon when closing the modal after playing and reopening it", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockResolvedValue({
      taskIds: ["task-1"],
      deduped: false,
    });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "芊悦 · 阳光正向的自然年轻女声" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    useTasksStore.setState({
      tasks: [
        {
          task_id: "task-1",
          project_name: "demo",
          task_type: "voice_sample",
          resource_id: "艾莉",
          status: "succeeded",
          result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
          updated_at: "2026-07-31T00:00:00Z",
        } as never,
      ],
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
    });
    fireEvent.play(document.querySelector("audio") as HTMLAudioElement);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
    });

    // 关闭弹窗(不经「重新生成」)再重新打开:isPreviewPlaying 须同样复位,否则重开后
    // 尚未播放的新会话会因这个陈旧的 true 值渲染成「暂停」。
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "暂停" })).not.toBeInTheDocument();
    });
  });

  it("keeps cancel disabled after the enqueue request resolves but before the task row lands in the store", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
    });
    // 手动控制 resolve 时机：模拟 enqueue 请求已成功返回、但下一次轮询把真实任务行
    // 写进 tasks-store 之前的那段空窗。
    let resolveEnqueue: (value: { taskIds: string[]; deduped: boolean }) => void;
    const enqueuePromise = new Promise<{ taskIds: string[]; deduped: boolean }>((resolve) => {
      resolveEnqueue = resolve;
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockReturnValue(enqueuePromise);

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "芊悦 · 阳光正向的自然年轻女声" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    });

    // enqueue 请求 resolve：taskId 落定为 task-1，但 tasks-store 里还没有这一行——
    // 空窗期间「取消」仍须保持禁用，否则用户可以关闭弹窗、丢失一个仍在合成且已计费
    // 任务的追踪入口。
    resolveEnqueue!({ taskIds: ["task-1"], deduped: false });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

    useTasksStore.setState({
      tasks: [
        {
          task_id: "task-1",
          project_name: "demo",
          task_type: "voice_sample",
          resource_id: "艾莉",
          status: "succeeded",
          result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
          updated_at: "2026-07-31T00:00:00Z",
        } as never,
      ],
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).not.toBeDisabled();
    });
  });

  it("hides the confirm button when the voice or text changes after a sample succeeds", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [
        { id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" },
        { id: "Serena", label: "苏瑶 · 温柔女声" },
      ],
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockResolvedValue({
      taskIds: ["task-1"],
      deduped: false,
    });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "芊悦 · 阳光正向的自然年轻女声" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    useTasksStore.setState({
      tasks: [
        {
          task_id: "task-1",
          project_name: "demo",
          task_type: "voice_sample",
          resource_id: "艾莉",
          status: "succeeded",
          result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
          updated_at: "2026-07-31T00:00:00Z",
        } as never,
      ],
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
    });

    // 用 Cherry 生成成功后改选 Serena：Confirm 此时仍指向 Cherry 合成的旧字节，继续显示
    // 会诱导用户以为确认的是当前选中的 Serena——须随选择变化一并隐藏，逼用户重新生成。
    fireEvent.change(screen.getByLabelText("音色"), { target: { value: "Serena" } });
    expect(screen.queryByRole("button", { name: "确认并保存" })).not.toBeInTheDocument();
  });
});
