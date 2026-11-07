import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

    const button = screen.getByRole("button", { name: "用 TTS 生成参考音频" });
    expect(button).toBeDisabled();
    expect(screen.getByText("请先在设置中配置音频供应商")).toBeInTheDocument();

    // Disabled entries neither open dialogs nor prefetch voices per character.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
    });
    // Prefill editable default copy by UI language.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
    });

    // Generate sample A, settling taskId to task-1.
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    // Sample A succeeds and plays, setting isPreviewPlaying true.
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

    // Regenerate clears taskId/unmounts old <audio>; reset isPreviewPlaying simultaneously,
    // or new samples display pause and cannot start.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
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

    // Close/reopen without regeneration must also reset isPreviewPlaying, or new unplayed sessions
    // would show pause from stale true state.
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
    // Manually resolve enqueue success while real task rows have not yet arrived
    // through the next tasks-store poll.
    let resolveEnqueue: (value: { taskIds: string[]; deduped: boolean }) => void;
    const enqueuePromise = new Promise<{ taskIds: string[]; deduped: boolean }>((resolve) => {
      resolveEnqueue = resolve;
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockReturnValue(enqueuePromise);

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    });

    // Enqueue resolves taskId task-1 without a tasks-store row yet;
    // cancel stays disabled during this window, preventing dialog close/lost tracking for still-synthesizing,
    // already-billed tasks.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
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

    // After Cherry succeeds, selecting Serena leaves Confirm pointing at Cherry bytes; leaving it visible
    // implies confirming Serena. Hide on selection changes, requiring regeneration.
    await userEvent.click(screen.getByRole("combobox", { name: "音色" }));
    await userEvent.click(await screen.findByRole("option", { name: "苏瑶 · 温柔女声" }));
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    // While Cherry/default-text synthesis is unfinished, lock voice/text so later Confirm cannot
    // mismatch newly displayed selections with old synthesis results actually saved.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    // Task row first arrives running.
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

    // New tasks evict it from the latest 200-row snapshot; latest observation remains
    // running and likely active server-side, so preserve occupancy/tracking for still-billing tasks
    // and avoid deduplicating new submissions into client-invisible tasks.
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveTextContent("芊悦 · 阳光正向的自然年轻女声");
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

    // Eviction after success is terminal; cached observations remain valid without relocking.
    // Confirm stays enabled, reusing validated artifacts despite snapshot trimming.
    useTasksStore.setState({ tasks: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
});
