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
      expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
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

  it("disables the voice and text inputs while a sample is generating", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
    });
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
    // 提交 Cherry/默认文案的合成尚未完成时，音色与文案须锁定，避免用户中途改选后 Confirm
    // 出现时挂着「界面显示新选择、实际保存的却是旧合成结果」的错配。
    await waitFor(() => {
      expect(screen.getByLabelText("音色")).toBeDisabled();
    });
    expect(screen.getByLabelText("试听文案")).toBeDisabled();

    resolveEnqueue!({ taskIds: ["task-1"], deduped: false });
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
      expect(screen.getByLabelText("音色")).toBeEnabled();
    });
  });

  it("keeps the modal locked when the running task row is evicted from the tasks-store snapshot", async () => {
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

    // 任务行先落地为 running。
    useTasksStore.setState({
      tasks: [
        {
          task_id: "task-1",
          project_name: "demo",
          task_type: "voice_sample",
          resource_id: "艾莉",
          status: "running",
          result: null,
          updated_at: "2026-07-31T00:00:00Z",
        } as never,
      ],
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    });

    // 大量新任务把这一行挤出 tasks-store 的最新 200 条快照——最后一次观察到的状态仍是
    // running，很可能服务端仍在跑，须继续按占用处理；放行会丢失一个仍在计费任务的追踪，
    // 或让随后的新提交被 dedupe 误合并进这个客户端已经看不到的任务。
    useTasksStore.setState({ tasks: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("keeps showing confirm from cached data after the succeeded task row is evicted", async () => {
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
      expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
    });

    // 成功后被挤出快照：已到终态，缓存的最后一次观察结果仍然有效，不应变回锁死态，
    // Confirm 依旧可点（复用已校验过的产物，不因快照裁剪而丢失）。
    useTasksStore.setState({ tasks: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
});
