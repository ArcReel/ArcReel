import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceSampleButton } from "./VoiceSampleButton";
import { API } from "@/api";
import * as generationActions from "@/actions/generation";
import { ApiRequestError } from "@/api/errors";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { defaultTaskStats, useTasksStore } from "@/stores/tasks-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import type { TaskItem } from "@/types";

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

async function submitAndEvictSample(task: TaskItem) {
  vi.useFakeTimers();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    useTasksStore.getState().setTasks([task]);
  });
  await act(async () => {
    useTasksStore.getState().setTasks([]);
  });
}

describe("VoiceSampleButton", () => {
  afterEach(() => {
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

    // 入口禁用即不打开弹窗，也不为每个角色预取音色列表
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
    });

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    });

    // enqueue 请求 resolve：taskId 落定为 task-1，但 tasks-store 里还没有这一行——
    // 空窗期间「取消」仍须保持禁用，否则用户可以关闭弹窗、丢失一个仍在合成且已计费
    // 任务的追踪入口。
    await act(async () => {
      resolveEnqueue!({ taskIds: ["task-1"], deduped: false });
    });
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
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

  it("unlocks when a running task evicted from the list becomes succeeded", async () => {
    const { task, generate } = mockVoiceSampleEnqueue();
    const lookup = vi.spyOn(API, "getTask")
      .mockResolvedValueOnce(task)
      .mockResolvedValue({
        ...task, status: "succeeded", result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
      });
    const confirm = vi.spyOn(API, "confirmCharacterVoiceSample").mockResolvedValue({
      success: true, path: "audio/艾莉.wav", url: "/audio/艾莉.wav",
    });
    const onSaved = vi.fn();

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={onSaved} />);
    await openSampleModal();
    await submitAndEvictSample(task);

    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByLabelText("试听文案")).toBeDisabled();
    const generating = screen.getByRole("button", { name: "生成中…" });
    expect(generating).toBeDisabled();
    fireEvent.click(generating);
    expect(generate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByLabelText("试听文案")).toBeEnabled();
    expect(lookup.mock.calls.at(-1)?.[0]).toBe(task.task_id);
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(play).toHaveBeenCalledOnce();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "确认并保存" }));
    });
    expect(confirm).toHaveBeenCalledWith("demo", "艾莉", task.task_id);
    expect(onSaved).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    ["failed", "重新生成", "语音生成失败"],
    ["cancelled", "生成", null],
  ] as const)("unlocks when a running task evicted from the list becomes %s", async (status, buttonName, errorMessage) => {
    const { task } = mockVoiceSampleEnqueue();
    vi.spyOn(API, "getTask")
      .mockResolvedValueOnce(task)
      .mockResolvedValue({ ...task, status, error_message: errorMessage });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();
    await submitAndEvictSample(task);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: buttonName })).toBeEnabled();
    expect(screen.getByLabelText("试听文案")).toBeEnabled();
    expect(screen.queryByRole("alert")?.textContent ?? null).toBe(errorMessage);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps a deduped task locked through lookup errors and clears its occupation once it settles", async () => {
    const { task, generate } = mockVoiceSampleEnqueue(true);
    vi.spyOn(API, "getTask")
      .mockRejectedValueOnce(new ApiRequestError("任务不存在", undefined, 404))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({ ...task, status: "cancelled" });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "生成" }));
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "音色" })).toBeDisabled();
    expect(generate).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    vi.useRealTimers();
    await openSampleModal();
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "确认并保存" })).not.toBeInTheDocument();
  });

  it("retries a timed-out lookup without unlocking until the retry confirms a terminal state", async () => {
    const { task, generate } = mockVoiceSampleEnqueue();
    const stalled = createDeferred<TaskItem>();
    const retry = createDeferred<TaskItem>();
    const lookup = vi.spyOn(API, "getTask")
      .mockReturnValueOnce(stalled.promise)
      .mockReturnValue(retry.promise);

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();
    await submitAndEvictSample(task);

    const cancel = screen.getByRole("button", { name: "取消" });
    const signal = lookup.mock.calls[0]?.[1]?.signal;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_999);
    });
    expect(lookup).toHaveBeenCalledOnce();
    expect(signal?.aborted).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });
    expect(lookup.mock.calls.map(([id]) => id)).toEqual([task.task_id, task.task_id]);
    expect(signal?.aborted).toBe(true);
    expect(lookup.mock.calls[1]?.[1]?.signal?.aborted).toBe(false);

    await act(async () => {
      stalled.resolve({ ...task, status: "failed", error_message: "旧查询结果" });
    });
    expect(cancel).toBeDisabled();
    expect(screen.getByLabelText("试听文案")).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(generate).toHaveBeenCalledOnce();

    await act(async () => {
      retry.resolve({ ...task, status: "cancelled" });
    });
    expect(cancel).toBeEnabled();
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
  });

  it("aborts an in-flight task lookup when the sample dialog unmounts", async () => {
    const { task } = mockVoiceSampleEnqueue();
    const pending = createDeferred<TaskItem>();
    const lookup = vi.spyOn(API, "getTask").mockReturnValue(pending.promise);

    const { unmount } = render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "生成" }));
    });
    const signal = lookup.mock.calls[0]?.[1]?.signal;
    expect(signal?.aborted).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      pending.resolve(task);
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(lookup).toHaveBeenCalledOnce();
  });

  it.each([
    { history: "never listed", listedBeforeEviction: false },
    { history: "evicted while running", listedBeforeEviction: true },
  ])("keeps a missing sample locked after returning until it settles ($history)", async ({ listedBeforeEviction }) => {
    const { task, generate } = mockVoiceSampleEnqueue(!listedBeforeEviction);
    const lookup = vi.spyOn(API, "getTask").mockResolvedValue(task);
    const newerTasks = Array.from({ length: 200 }, (_, index) => makeTask({
      task_id: `newer-${index}`,
      project_name: "demo",
      status: "succeeded",
    }));
    let recent = listedBeforeEviction ? [task] : newerTasks;
    vi.spyOn(API, "listTasks").mockImplementation(async ({ projectName } = {}) => {
      const items = projectName === "demo" ? recent : [];
      return { items, total: items === newerTasks ? 201 : items.length, page: 1, page_size: 200 };
    });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });

    const { setRefreshScope, refreshTasks } = useTasksStore.getState();
    setRefreshScope({ projectName: "demo" });
    const props = { projectName: "demo", characterName: "艾莉", onSaved: vi.fn() };
    const { unmount } = render(<VoiceSampleButton {...props} />);
    await openSampleModal();
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "生成" }));
    });
    expect(lookup).toHaveBeenCalledOnce();
    lookup.mockClear();
    await act(refreshTasks);
    expect(useTasksStore.getState().tasks).toEqual(recent);
    expect(lookup).toHaveBeenCalledTimes(listedBeforeEviction ? 0 : 1);
    recent = newerTasks;
    await act(refreshTasks);
    expect(useTasksStore.getState().tasks).toEqual(newerTasks);
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    unmount();
    vi.useRealTimers();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    lookup.mockClear();

    setRefreshScope({ projectName: "other" });
    await refreshTasks();
    expect(lookup).not.toHaveBeenCalled();

    setRefreshScope({ projectName: "demo" });
    await refreshTasks();
    render(<VoiceSampleButton {...props} />);
    const entry = screen.getByRole("button", { name: /用 TTS 生成参考音频/ });
    expect(entry).toBeDisabled();
    expect(lookup).toHaveBeenCalledExactlyOnceWith(task.task_id, { signal: expect.any(AbortSignal) });

    for (const status of [404, 500]) {
      lookup.mockRejectedValueOnce(new ApiRequestError("查询任务失败", undefined, status));
      await act(refreshTasks);
      expect(entry).toBeDisabled();
      fireEvent.click(entry);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(generate).toHaveBeenCalledOnce();
    }

    lookup.mockResolvedValue({ ...task, status: "cancelled" });
    await act(refreshTasks);
    expect(entry).toBeEnabled();
    await openSampleModal();
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
  });

  it("keeps confirm enabled when a stale running row arrives after a terminal lookup", async () => {
    const { task } = mockVoiceSampleEnqueue();
    const succeeded = {
      ...task, status: "succeeded" as const, result: { file_path: "audio/voice_sample__艾莉__task-1.wav" },
    };
    const lookup = createDeferred<TaskItem>();
    const list = createDeferred<Awaited<ReturnType<typeof API.listTasks>>>();
    vi.spyOn(API, "getTask").mockReturnValue(lookup.promise);
    const listTasks = vi.spyOn(API, "listTasks").mockReturnValueOnce(list.promise);
    vi.spyOn(API, "getTaskStats").mockResolvedValue({ stats: defaultTaskStats });
    const { refreshTasks, setRefreshScope } = useTasksStore.getState();
    setRefreshScope({ projectName: "demo" });

    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    await openSampleModal();
    await submitAndEvictSample(task);
    const refresh = refreshTasks();
    await act(async () => {
      lookup.resolve(succeeded);
    });
    const confirm = screen.getByRole("button", { name: "确认并保存" });
    await act(async () => {
      list.resolve({ items: [task], total: 1, page: 1, page_size: 200 });
      await refresh;
    });

    expect(confirm).toBeEnabled();
    expect(useTasksStore.getState().tasks[0]?.status).toBe("succeeded");

    const assetTask = { ...task, task_id: "asset-task", task_type: "character" };
    listTasks.mockResolvedValueOnce({
      items: [succeeded, assetTask], total: 2, page: 1, page_size: 200,
    });
    await act(refreshTasks);
    expect(confirm).toBeDisabled();
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
      expect(screen.getByRole("combobox", { name: "音色" })).toHaveValue("Cherry");
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
    await act(async () => {
      useTasksStore.setState({ tasks: [] });
    });
    expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
});
