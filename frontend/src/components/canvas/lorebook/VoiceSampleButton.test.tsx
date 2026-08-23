import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as generationActions from "@/actions/generation";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useTasksStore } from "@/stores/tasks-store";
import { VoiceSampleButton } from "./VoiceSampleButton";

function configure(media: string[]) {
  useConfigStatusStore.setState({ availableMediaTypes: media });
}

function noCandidate() {
  vi.spyOn(API, "getCharacterVoiceSampleCandidate").mockResolvedValue({ candidate: null });
}

describe("VoiceSampleButton", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useConfigStatusStore.setState({ availableMediaTypes: [] });
  });

  it("opens in video-extraction mode by default without loading TTS voices", async () => {
    configure(["image", "video", "audio"]);
    noCandidate();
    const voices = vi.spyOn(API, "getAudioBackendVoices");
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "生成角色参考音频" }));

    expect(screen.getByRole("button", { name: "视频提取（默认）" })).toBeInTheDocument();
    expect(screen.getByText(/独白视频不会展示或保存/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/我是艾莉/)).toBeInTheDocument();
    await waitFor(() => expect(API.getCharacterVoiceSampleCandidate).toHaveBeenCalled());
    expect(voices).not.toHaveBeenCalled();
  });

  it("keeps voice generation available while the character image slot is busy", () => {
    configure(["video"]);
    noCandidate();
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" busy onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "生成角色参考音频" })).not.toBeDisabled();
  });

  it("closes immediately while submitting the default video strategy in the background", async () => {
    configure(["video"]);
    noCandidate();
    let resolveEnqueue: ((value: { taskIds: string[]; deduped: boolean }) => void) | undefined;
    const enqueue = vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveEnqueue = resolve;
        }),
    );
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "生成角色参考音频" }));
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    expect(screen.queryByRole("heading", { name: "生成语音参考样本" })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(enqueue).toHaveBeenCalledWith(
        "demo",
        "艾莉",
        expect.objectContaining({ strategy: "video", text: expect.stringContaining("艾莉") }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "生成角色参考音频" }));
    expect(screen.getByRole("heading", { name: "生成语音参考样本" })).toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(cancel).not.toBeDisabled();
    fireEvent.click(cancel);
    expect(screen.queryByRole("heading", { name: "生成语音参考样本" })).not.toBeInTheDocument();

    resolveEnqueue?.({ taskIds: ["task-video"], deduped: false });
  });

  it("preserves TTS as an optional mode and submits its selected voice", async () => {
    configure(["video", "audio"]);
    noCandidate();
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦" }],
    });
    const enqueue = vi.spyOn(generationActions, "enqueueCharacterVoiceSample").mockResolvedValue({
      taskIds: ["task-tts"],
      deduped: false,
    });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "生成角色参考音频" }));
    fireEvent.click(screen.getByRole("button", { name: "TTS 音色" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "芊悦" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => {
      expect(enqueue).toHaveBeenCalledWith(
        "demo",
        "艾莉",
        expect.objectContaining({ strategy: "tts", voice: "Cherry" }),
      );
    });
  });

  it("falls back to TTS when video generation is unavailable", async () => {
    configure(["audio"]);
    noCandidate();
    vi.spyOn(API, "getAudioBackendVoices").mockResolvedValue({
      configured: true,
      provider_id: "dashscope",
      model: "qwen3-tts-flash",
      voices: [{ id: "Cherry", label: "芊悦" }],
    });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /视频生成不可用/ }));
    await waitFor(() => expect(screen.getByRole("option", { name: "芊悦" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "视频提取（默认）" })).toBeDisabled();
  });

  it("restores an automatically generated audio candidate for preview and confirmation", async () => {
    configure(["video"]);
    vi.spyOn(API, "getCharacterVoiceSampleCandidate").mockResolvedValue({
      candidate: {
        task_id: "auto-task",
        project_name: "demo",
        task_type: "voice_sample",
        media_type: "video",
        resource_id: "艾莉",
        resource_type: null,
        script_file: null,
        payload: { strategy: "video", monologue: "自动独白" },
        status: "succeeded",
        result: { file_path: "audio/candidate.wav" },
        error_message: null,
        cancelled_by: null,
        provider_id: "ark",
        provider_job_id: null,
        source: "agent",
        queued_at: "2026-08-01T00:00:00Z",
        started_at: "2026-08-01T00:00:01Z",
        finished_at: "2026-08-01T00:00:10Z",
        updated_at: "2026-08-01T00:00:10Z",
      },
    });
    render(<VoiceSampleButton projectName="demo" characterName="艾莉" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "生成角色参考音频" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认并保存" })).toBeInTheDocument();
      expect(screen.getByDisplayValue("自动独白")).toBeInTheDocument();
    });
  });
});
