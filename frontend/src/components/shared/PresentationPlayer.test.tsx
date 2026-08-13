import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { PresentationReadModel } from "@/types/presentation";
import { PresentationPlayer } from "./PresentationPlayer";

vi.mock("@/api", () => ({
  API: {
    getPresentation: vi.fn(),
    getFileUrl: vi.fn((_project: string, path: string) => `/media/${path}`),
    downloadPresentationBundle: vi.fn(),
  },
}));

const post: PresentationReadModel = {
  schema_version: 1,
  episode: 1,
  resource_type: "videos",
  script_file: "episode_1.json",
  transition_to_next: "cut",
  subtitle_artifact_path: "subtitles/post.json",
  presentation_artifact_path: "presentations/post.json",
  persisted: true,
  unit_id: "E1S01",
  variant: "post_production",
  speech_mode: "narrator_voiceover",
  selection: "current",
  currency: "stale",
  video: {
    artifact_path: "versions/videos/E1S01_v3.mp4",
    version: 3,
    selection: "current",
    currency: "stale",
    basis: { kind: "artifact-components/video", kind_version: 1, digest: "sha256-v1:v" },
    content_digest: "sha256-v1:video",
    actual_duration_seconds: 6,
    start_microseconds: 0,
    duration_microseconds: 6_000_000,
    audio_enabled: false,
    gain: 0,
  },
  narration_audio: null,
  subtitles: [
    { start_microseconds: 0, duration_microseconds: 6_000_000, text: "机械字幕", owner: "narrator", speaker: null },
  ],
  subtitle_basis: { kind: "artifact-speech/mechanical-subtitle", kind_version: 1, digest: "sha256-v1:s" },
  presentation_basis: { kind: "artifact-speech/presentation", kind_version: 1, digest: "sha256-v1:p" },
  timing: "mechanical",
  subtitles_adjustable: true,
};

const tts: PresentationReadModel = {
  ...post,
  variant: "use_tts",
  currency: "current",
  video: { ...post.video, currency: "current", audio_enabled: true, gain: 1 },
  narration_audio: {
    artifact_path: "versions/audio/E1S01_v2.wav",
    version: 2,
    selection: "current",
    currency: "current",
    basis: { kind: "narration-delivery/tts-audio", kind_version: 1, digest: "sha256-v1:a" },
    content_digest: "sha256-v1:audio",
    actual_duration_seconds: 4.5,
    start_microseconds: 0,
    duration_microseconds: 4_500_000,
    gain: 1,
  },
};

describe("PresentationPlayer", () => {
  beforeEach(() => {
    vi.mocked(API.getPresentation).mockImplementation(async (_project, _type, _id, options) =>
      options?.variant === "use_tts" ? tts : post,
    );
    vi.mocked(API.downloadPresentationBundle).mockResolvedValue({
      blob: new Blob(["zip"]),
      filename: "presentation.zip",
    });
    globalThis.URL.createObjectURL = vi.fn(() => "blob:presentation");
    globalThis.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  it("renders the selected immutable video, explicit audio-off, subtitle track, and status", async () => {
    render(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );

    const video = await screen.findByLabelText("E1S01 成片预览");
    expect(video).toHaveAttribute("src", "/media/versions/videos/E1S01_v3.mp4");
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveProperty("volume", 0);
    Object.defineProperty(video, "muted", { configurable: true, writable: true, value: false });
    Object.defineProperty(video, "volume", { configurable: true, writable: true, value: 0.5 });
    fireEvent.volumeChange(video);
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveProperty("volume", 0);
    expect(video.querySelector("track")).toHaveAttribute("kind", "captions");
    expect(screen.getByText("已过期")).toBeInTheDocument();
    expect(screen.getByText("机械字幕")).toBeInTheDocument();
    expect(API.getPresentation).toHaveBeenCalledWith(
      "demo",
      "videos",
      "E1S01",
      expect.objectContaining({ variant: "post_production" }),
    );
  });

  it("switches to TTS, synchronizes its unity track, and pins bundle versions", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    const user = userEvent.setup();
    render(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );
    await screen.findByLabelText("E1S01 成片预览");

    await user.click(screen.getByRole("button", { name: "TTS 叠加" }));
    const video = await screen.findByLabelText("E1S01 成片预览");
    const audio = await screen.findByLabelText("E1S01 TTS 音轨");
    expect(video).toHaveProperty("muted", false);
    expect(audio).toHaveAttribute("src", "/media/versions/audio/E1S01_v2.wav");
    fireEvent.play(video);
    await waitFor(() => expect(play).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "下载可编辑包" }));
    expect(API.downloadPresentationBundle).toHaveBeenCalledWith(
      "demo",
      "videos",
      "E1S01",
      expect.objectContaining({ variant: "use_tts", videoVersion: 3, audioVersion: 2 }),
    );
  });

  it("requests an explicit historical version without restoring it", async () => {
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="reference_videos"
        resourceId="E1U01"
        videoVersion={7}
      />,
    );

    await screen.findByLabelText("E1S01 成片预览");
    expect(API.getPresentation).toHaveBeenCalledWith(
      "demo",
      "reference_videos",
      "E1U01",
      expect.objectContaining({ videoVersion: 7 }),
    );
  });

  it("discards a late response after the requested unit changes", async () => {
    let resolveFirst: ((value: PresentationReadModel) => void) | undefined;
    let resolveSecond: ((value: PresentationReadModel) => void) | undefined;
    vi.mocked(API.getPresentation).mockImplementation(
      (_project, _type, id) =>
        new Promise<PresentationReadModel>((resolve) => {
          if (id === "E1S01") resolveFirst = resolve;
          else resolveSecond = resolve;
        }),
    );
    const { rerender } = render(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );

    rerender(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S02" />,
    );
    resolveSecond?.({
      ...post,
      unit_id: "E1S02",
      video: { ...post.video, artifact_path: "versions/videos/E1S02_v1.mp4" },
    });
    expect(await screen.findByLabelText("E1S02 成片预览")).toHaveAttribute(
      "src",
      "/media/versions/videos/E1S02_v1.mp4",
    );

    resolveFirst?.(post);
    await waitFor(() => {
      expect(screen.getByLabelText("E1S02 成片预览")).toHaveAttribute(
        "src",
        "/media/versions/videos/E1S02_v1.mp4",
      );
    });
    expect(screen.queryByLabelText("E1S01 成片预览")).not.toBeInTheDocument();
  });
});
