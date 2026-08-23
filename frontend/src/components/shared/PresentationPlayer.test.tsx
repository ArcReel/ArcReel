import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { PresentationReadModel } from "@/types/presentation";
import { PresentationPlayer } from "./PresentationPlayer";

const post: PresentationReadModel = {
  schema_version: 1,
  provenance: "verified",
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
  subtitles_webvtt: "WEBVTT\n\n1\n00:00:00.000 --> 00:00:06.000\n机械字幕\n",
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
    useProjectsStore.setState({ assetFingerprints: {}, projectSnapshotRevisions: {} });
    vi.spyOn(API, "getPresentation").mockImplementation(async (_project, _type, _id, options) =>
      options?.variant === "use_tts" ? tts : post,
    );
    vi.spyOn(API, "downloadPresentationBundle").mockResolvedValue({
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
    expect(video).toHaveAttribute(
      "src",
      "/api/v1/files/demo/versions/videos/E1S01_v3.mp4?v=sha256-v1%3Avideo",
    );
    expect(video).toHaveProperty("muted", true);
    await waitFor(() => expect(video).toHaveProperty("volume", 0));
    Object.defineProperty(video, "muted", { configurable: true, writable: true, value: false });
    Object.defineProperty(video, "volume", { configurable: true, writable: true, value: 0.5 });
    fireEvent.volumeChange(video);
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveProperty("volume", 0.5);
    expect(video.querySelector("track")).toHaveAttribute("kind", "captions");
    expect(screen.getByText("当前版本")).toBeInTheDocument();
    expect(screen.getByText("比当前内容旧")).toBeInTheDocument();
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
    expect(screen.getByText("当前版本")).toBeInTheDocument();
    expect(screen.getByText("与当前内容一致")).toBeInTheDocument();
    expect(video).toHaveProperty("muted", false);
    expect(audio).toHaveAttribute(
      "src",
      "/api/v1/files/demo/versions/audio/E1S01_v2.wav?v=sha256-v1%3Aaudio",
    );
    fireEvent.play(video);
    await waitFor(() => expect(play).toHaveBeenCalled());

    Object.defineProperty(video, "volume", { configurable: true, writable: true, value: 0.4 });
    Object.defineProperty(video, "muted", { configurable: true, writable: true, value: true });
    Object.defineProperty(video, "playbackRate", { configurable: true, writable: true, value: 1.5 });
    fireEvent.volumeChange(video);
    fireEvent.rateChange(video);
    expect(audio).toHaveProperty("volume", 0.4);
    expect(audio).toHaveProperty("muted", true);
    expect(audio).toHaveProperty("playbackRate", 1.5);

    await user.click(screen.getByRole("button", { name: "下载可编辑包" }));
    expect(API.downloadPresentationBundle).toHaveBeenCalledWith(
      "demo",
      "videos",
      "E1S01",
      expect.objectContaining({ variant: "use_tts", videoVersion: 3, audioVersion: 2 }),
    );
  });

  it("reloads the shared model when the selected narration audio changes", async () => {
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
        initialVariant="use_tts"
      />,
    );
    await screen.findByLabelText("E1S01 TTS 音轨");
    expect(API.getPresentation).toHaveBeenCalledTimes(1);

    act(() => {
      useProjectsStore.getState().updateAssetFingerprints({
        "audio/segment_E1S01.wav": 2,
      });
    });

    await waitFor(() => expect(API.getPresentation).toHaveBeenCalledTimes(2));
  });

  it("reloads the shared model when canonical project or script input refreshes", async () => {
    render(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );
    await screen.findByLabelText("E1S01 成片预览");
    expect(API.getPresentation).toHaveBeenCalledTimes(1);

    act(() => {
      useProjectsStore.getState().setCurrentProject("demo", { title: "Demo" } as ProjectData, {});
    });

    await waitFor(() => expect(API.getPresentation).toHaveBeenCalledTimes(2));
  });

  it("keeps TTS at unity when the provider track is explicitly disabled", async () => {
    vi.spyOn(API, "getPresentation").mockResolvedValue({
      ...tts,
      video: { ...tts.video, audio_enabled: false, gain: 0 },
    });
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
        initialVariant="use_tts"
      />,
    );

    const video = await screen.findByLabelText("E1S01 成片预览");
    const audio = await screen.findByLabelText("E1S01 TTS 音轨");
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveProperty("volume", 1);
    expect(audio).toHaveProperty("muted", false);
    expect(audio).toHaveProperty("volume", 1);

    Object.defineProperty(video, "muted", { configurable: true, writable: true, value: false });
    Object.defineProperty(video, "volume", { configurable: true, writable: true, value: 0.25 });
    fireEvent.volumeChange(video);
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveProperty("volume", 0.25);
    expect(audio).toHaveProperty("muted", false);
    expect(audio).toHaveProperty("volume", 0.25);
  });

  it("pauses TTS while video buffers and resynchronizes when playback resumes", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
        initialVariant="use_tts"
      />,
    );

    const video = await screen.findByLabelText("E1S01 成片预览");
    const audio = await screen.findByLabelText("E1S01 TTS 音轨");
    Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: 2 });
    Object.defineProperty(audio, "currentTime", { configurable: true, writable: true, value: 0 });

    play.mockClear();
    pause.mockClear();
    fireEvent.waiting(video);
    expect(pause).toHaveBeenCalledTimes(1);

    fireEvent.playing(video);
    expect(audio).toHaveProperty("currentTime", 2);
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));

    pause.mockClear();
    fireEvent.stalled(video);
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it("keeps rendition recovery available when a TTS presentation cannot be built", async () => {
    vi.spyOn(API, "getPresentation").mockImplementation(async (_project, _type, _id, options) => {
      if (options?.variant === "use_tts") throw new Error("TTS unavailable");
      return post;
    });
    const user = userEvent.setup();
    render(
      <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
    );
    await screen.findByLabelText("E1S01 成片预览");

    await user.click(screen.getByRole("button", { name: "TTS 叠加" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("TTS unavailable");
    await user.click(screen.getByRole("button", { name: "原音成片" }));

    expect(await screen.findByLabelText("E1S01 成片预览")).toBeInTheDocument();
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
    vi.spyOn(API, "getPresentation").mockImplementation(
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
    const secondUrl = "/api/v1/files/demo/versions/videos/E1S02_v1.mp4?v=sha256-v1%3Avideo";
    expect(await screen.findByLabelText("E1S02 成片预览")).toHaveAttribute("src", secondUrl);

    resolveFirst?.(post);
    await waitFor(() => {
      expect(screen.getByLabelText("E1S02 成片预览")).toHaveAttribute("src", secondUrl);
    });
    expect(screen.queryByLabelText("E1S01 成片预览")).not.toBeInTheDocument();
  });

  it("pauses active media before loading another rendition", async () => {
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
        initialVariant="use_tts"
      />,
    );
    await screen.findByLabelText("E1S01 TTS 音轨");
    pause.mockClear();

    await user.click(screen.getByRole("button", { name: "原音成片" }));

    expect(pause).toHaveBeenCalledTimes(2);
  });

  it("stops browser playback at the modeled video-stream boundary", async () => {
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    render(
      <PresentationPlayer
        projectName="demo"
        resourceType="videos"
        resourceId="E1S01"
        initialVariant="use_tts"
      />,
    );
    const video = await screen.findByLabelText("E1S01 成片预览");
    await screen.findByLabelText("E1S01 TTS 音轨");
    Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: 6.2 });
    pause.mockClear();

    fireEvent.timeUpdate(video);

    expect(video).toHaveProperty("currentTime", 6);
    expect(pause).toHaveBeenCalledTimes(2);
  });

  it("suppresses the custom cue while native captions are showing", async () => {
    const nativeTrack = { mode: "hidden" };
    const textTracks = new EventTarget() as EventTarget & { length: number; 0: typeof nativeTrack };
    Object.defineProperties(textTracks, {
      length: { configurable: true, value: 1 },
      0: { configurable: true, value: nativeTrack },
    });
    const priorDescriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "textTracks");
    Object.defineProperty(HTMLMediaElement.prototype, "textTracks", {
      configurable: true,
      get: () => textTracks,
    });
    try {
      render(
        <PresentationPlayer projectName="demo" resourceType="videos" resourceId="E1S01" />,
      );
      await screen.findByLabelText("E1S01 成片预览");
      expect(screen.getByText("机械字幕")).toBeInTheDocument();

      nativeTrack.mode = "showing";
      act(() => textTracks.dispatchEvent(new Event("change")));

      expect(screen.queryByText("机械字幕")).not.toBeInTheDocument();
    } finally {
      if (priorDescriptor) {
        Object.defineProperty(HTMLMediaElement.prototype, "textTracks", priorDescriptor);
      } else {
        Reflect.deleteProperty(HTMLMediaElement.prototype, "textTracks");
      }
    }
  });
});
