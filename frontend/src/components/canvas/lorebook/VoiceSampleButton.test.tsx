import { seedTaskState } from "@/test/task-state";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceSampleButton } from "./VoiceSampleButton";
import { API } from "@/api";
import * as generationActions from "@/actions/generation";
import { ApiRequestError } from "@/api/errors";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { selectRecentTasks, defaultTaskStats, useTasksStore } from "@/stores/tasks-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";

function setAudioConfigured(configured: boolean) {
  useConfigStatusStore.setState({
    availableMediaTypes: configured ? ["image", "video", "text", "audio"] : ["image", "video", "text"],
  });
}

function mockVoiceSampleEnqueue(deduped = false) {
  setAudioConfigured(true);
  vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
    configured: true,
    provider_id: "dashscope",
    model: "qwen3-tts-flash",
    voices: [{ id: "Cherry", label: "芊悦 · 阳光正向的自然年轻女声" }],
  });
  const task = makeTask({
    task_id: "task-1",
    project_name: "demo",
    task_type: "voice_sample",
    media_type: "audio",
    resource_id: "艾莉",
    status: "running",
  });
  const generate = vi.spyOn(API, "generateCharacterVoiceSample").mockResolvedValue({
    success: true, task_id: task.task_id, deduped, message: "",
  });
  return { task, generate };
}

async function openSampleModal() {
  fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
  await waitFor(() => {
    expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
  });
}

function mockTaskRefresh() {
  const active = vi.spyOn(API, "getActiveTasks").mockResolvedValue({ items: [], truncated: false });
  const history = Array.from({ length: 200 }, (_, i) =>
    makeTask({ project_name: "demo", task_id: `history-${i}`, status: "succeeded" }),
  );
  const recent = vi.spyOn(API, "listTasks").mockResolvedValue({ items: history, total: 201, page: 1, page_size: 200 });
  vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
  return { active, recent };
}

async function submitSample() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
  });
}

describe("VoiceSampleButton", () => {
  beforeEach(() => useTasksStore.getState().setRefreshScope({ projectName: "demo" }));

  afterEach(() => {
    useTasksStore.getState().setRefreshScope(null);
    vi.useRealTimers();
    vi.restoreAllMocks();
    useTasksStore.setState(useTasksStore.getInitialState(), true);
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });
    expect(screen.getByDisplayValue(/这是一段声音示例/)).toBeInTheDocument();
  });

  it("lets the voice id be typed when the model has no voice list", async () => {
    setAudioConfigured(true);
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "custom-1",
      model: "qwen-audio-3.1-tts-next",
      voices: [],
    });
    vi.spyOn(API, "generateCharacterVoiceSample").mockResolvedValue({ success: true, task_id: "task-1", deduped: false, message: "" });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await screen.findByText("当前 TTS 模型没有可选音色，照供应商文档填写音色 id");

    const voiceInput = screen.getByRole("combobox", { name: "音色" });
    expect(voiceInput).toBeEnabled();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
    fireEvent.change(voiceInput, { target: { value: " my-cloned-voice " } });
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() =>
      expect(API.generateCharacterVoiceSample).toHaveBeenCalledWith(
        "demo",
        "艾莉",
        expect.any(String),
        "my-cloned-voice",
      ),
    );
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    seedTaskState({
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    seedTaskState({
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
    let resolveEnqueue: (value: { taskIds: string[]; deduped: boolean }) => void;
    const enqueuePromise = new Promise<{ taskIds: string[]; deduped: boolean }>((resolve) => {
      resolveEnqueue = resolve;
    });
    vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockReturnValue(enqueuePromise);

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /用 TTS 生成参考音频/ }));
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    });

    await act(async () => {
      resolveEnqueue!({ taskIds: ["task-1"], deduped: false });
    });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

    seedTaskState({
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(generationActions.enqueueCharacterVoiceSample).toHaveBeenCalled();
    });

    seedTaskState({
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

    const voiceInput = screen.getByRole("combobox", { name: "音色" });
    await userEvent.clear(voiceInput);
    await userEvent.click(voiceInput);
    await userEvent.click(await screen.findByRole("option", { name: "Serena · 苏瑶 · 温柔女声" }));
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(screen.getByLabelText("音色")).toBeDisabled();
    });
    expect(screen.getByLabelText("试听文案")).toBeDisabled();

    resolveEnqueue!({ taskIds: ["task-1"], deduped: false });
    seedTaskState({
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

  it("generates and confirms an evicted sample during character image generation", async () => {
    const { task } = mockVoiceSampleEnqueue();
    const { active, recent } = mockTaskRefresh();
    const imageTask = makeTask({
      task_id: "character-image", project_name: "demo", task_type: "character",
      media_type: "image", resource_id: "艾莉", status: "running",
    });
    active.mockResolvedValue({ items: [imageTask], truncated: false });
    active.mockResolvedValueOnce({ items: [imageTask, task], truncated: false });
    recent.mockResolvedValueOnce({ items: [task], total: 1, page: 1, page_size: 200 });
    const lookup = vi.spyOn(API, "lookupTasks").mockResolvedValue({
      items: [{ ...task, status: "succeeded", result: { file_path: "audio/voice_sample__艾莉__task-1.wav" } }],
      missing_ids: [],
    });
    const confirm = vi.spyOn(API, "confirmCharacterVoiceSample").mockResolvedValue({
      success: true, path: "audio/艾莉.wav", url: "/audio/艾莉.wav",
    });
    const onSaved = vi.fn();
    const { refreshTasks, setRefreshScope } = useTasksStore.getState();
    setRefreshScope({ projectName: "demo" });
    seedTaskState({ tasks: [imageTask] });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={onSaved} />);
    await openSampleModal();
    await submitSample();

    await act(refreshTasks);
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    await act(refreshTasks);

    expect(selectRecentTasks(useTasksStore.getState())).toHaveLength(200);
    expect(selectRecentTasks(useTasksStore.getState()).some((row) => row.task_id === task.task_id)).toBe(false);
    expect(lookup).toHaveBeenCalledWith([task.task_id], "demo", expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "播放" })).toBeEnabled();

    active.mockResolvedValueOnce({ items: [imageTask, task], truncated: false });
    await act(refreshTasks);
    expect(screen.getByRole("button", { name: "确认并保存" })).toBeEnabled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "确认并保存" }));
    });
    expect(confirm).toHaveBeenCalledWith("demo", "艾莉", task.task_id);
    expect(onSaved).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(useTasksStore.getState().tracked.has(task.task_id)).toBe(false);
  });

  it("keeps waiting through a timeout and a 503, then offers to regenerate when the result is missing", async () => {
    const { task } = mockVoiceSampleEnqueue(true);
    mockTaskRefresh();
    const lookupStarted = createDeferred<void>();
    vi.spyOn(API, "lookupTasks")
      .mockImplementationOnce((_ids, _project, signal) => {
        lookupStarted.resolve();
        return new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        });
      })
      .mockRejectedValueOnce(new ApiRequestError("服务暂不可用", undefined, 503))
      .mockResolvedValueOnce({ items: [], missing_ids: [task.task_id] });
    const { refreshTasks, setRefreshScope } = useTasksStore.getState();
    setRefreshScope({ projectName: "demo" });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();
    await submitSample();

    vi.useFakeTimers();
    await act(async () => {
      const done = refreshTasks();
      await lookupStarted.promise;
      await vi.advanceTimersByTimeAsync(10_000);
      await done;
    });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    await act(refreshTasks);
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

    await act(refreshTasks);
    expect(screen.getByRole("alert")).toHaveTextContent("试听结果已不可用，请重新生成");
    expect(screen.getByRole("button", { name: "重新生成" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(useTasksStore.getState().tracked.has(task.task_id)).toBe(false);
  });

  it("unlocks and shows the failure of an evicted sample", async () => {
    const { task } = mockVoiceSampleEnqueue();
    mockTaskRefresh();
    vi.spyOn(API, "lookupTasks").mockResolvedValue({
      items: [{ ...task, status: "failed", error_message: "语音生成失败" }],
      missing_ids: [],
    });
    const { refreshTasks, setRefreshScope } = useTasksStore.getState();
    setRefreshScope({ projectName: "demo" });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();
    await submitSample();

    await act(refreshTasks);

    expect(screen.getByRole("alert")).toHaveTextContent("语音生成失败");
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "重新生成" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "确认并保存" })).not.toBeInTheDocument();
  });
});
