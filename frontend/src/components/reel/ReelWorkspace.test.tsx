import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { stubMediaElementPlayback } from "@/test/media-element";
import type { DramaEpisodeScript } from "@/types";
import type { EditClip, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { ReelWorkspace } from "./ReelWorkspace";

const { sendMessage, interrupt } = vi.hoisted(() => ({ sendMessage: vi.fn(), interrupt: vi.fn() }));
vi.mock("@/hooks/useAssistantSession", () => ({ useAssistantSession: () => ({ sendMessage, answerQuestion: vi.fn(), interrupt }) }));
vi.mock("@/hooks/useTaskRefresh", () => ({ useTaskRefresh: () => undefined }));
vi.mock("@/hooks/useProjectEventsSSE", () => ({ useProjectEventsSSE: () => undefined }));

function summary(id: string, name: string, updated: string): EditTimelineSummary {
  return { id, name, episode: 1, revision: 1, clip_count: 2, created_at: updated, updated_at: updated, updated_by: { kind: "creator", user_id: null }, update_summary: "" };
}
function clip(id: string, start: number): EditClip {
  return { id, unit_id: id, status: "ready", start, duration: 4, video_version: 1, source_duration: 5, trim: null, source_volume: 0, hold: 0, carries_narration: false, narration: null, reason: null, transition_to_next: null };
}
function timeline(id: string): EditTimelineReadout {
  return { timeline: { id, name: id, episode: 1 }, revision: 1, latest_revision: 1, duration: 8, clips: [clip("E1S01", 0), clip("E1S02", 4)], bgm: [], issues: [] };
}

describe("ReelWorkspace", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    stubMediaElementPlayback();
    sendMessage.mockReset().mockResolvedValue(true);
    interrupt.mockReset();
    useAssistantStore.setState({ entries: [], sending: false, sessionStatus: "idle", pendingQuestion: null, error: null });
    useProjectsStore.setState({ assetFingerprints: {}, currentScripts: {}, currentProjectData: { title: "Late Again", style: "3D", content_mode: "drama", characters: {}, episodes: [{ episode: 1, title: "Late Again", script_file: "" }] } });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [summary("old", "Earlier cut", "2026-10-01"), summary("new", "Revised cut", "2026-10-02")] });
    vi.spyOn(API, "getEditTimeline").mockImplementation(async (_project, id) => timeline(id));
    vi.spyOn(API, "getFinalCutStatus").mockImplementation(async (_project, id) => ({ episode: 1, timeline_id: id, status: "current", artifact_path: `${id}.mp4`, version: 1, rendered_at: null, narration: "without_narration", subtitles: "no_subtitles", download_url: `/media/${id}.mp4` }));
  });

  function setRenderedShots(ids: number[]) {
    const script: DramaEpisodeScript = {
      episode: 1, title: "Late Again", content_mode: "drama", novel: { title: "", chapter: "" },
      scenes: [1, 2, 3].map((number) => ({
        scene_id: `E1S0${number}`, duration_seconds: 5, segment_break: false, characters_in_scene: [], image_prompt: null, video_prompt: null,
        generated_assets: { video_clip: ids.includes(number) ? `videos/shot${number}.mp4` : null, video_thumbnail: null, storyboard_image: null, storyboard_last_image: null, grid_id: null, grid_cell_index: null, video_uri: null, status: "completed" },
      })),
    };
    useProjectsStore.setState({ currentScripts: { "episode_1.json": script }, currentProjectData: { title: "Late Again", style: "3D", content_mode: "drama", characters: {}, episodes: [{ episode: 1, title: "Late Again", script_file: "scripts/episode_1.json" }] } });
  }

  it("previews completed shots outside the stitched cut and scopes feedback to the actual shot", async () => {
    setRenderedShots([1, 3]);
    render(<ReelWorkspace projectName="film" episode={1} />);
    await screen.findByLabelText("Episode screening");
    expect(screen.queryByRole("button", { name: "Preview shot 2" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview shot 3" }));
    const raw = screen.getByLabelText<HTMLVideoElement>("Episode screening");
    expect(raw).toHaveAttribute("src", API.getFileUrl("film", "videos/shot3.mp4", null));
    fireEvent.loadedMetadata(raw);
    expect(raw.currentTime).toBe(0);
    expect(raw.play).toHaveBeenCalled();
    expect(screen.getByText("Playing shot 3 · individual take, not the finished cut")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Fix the paper grip" } });
    fireEvent.click(screen.getByRole("button", { name: "Make a revised cut" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledOnce());
    expect(sendMessage.mock.calls[0][0]).toContain("shot E1S03 and only the adjacent transitions");
    fireEvent.click(screen.getByRole("button", { name: "Watch stitched cut" }));
    expect(screen.getByLabelText("Episode screening")).toHaveAttribute("src", "/media/new.mp4");
    expect(screen.queryByText("Playing shot 3 · individual take, not the finished cut")).not.toBeInTheDocument();
  });

  it("shows newly rendered shots without clearing feedback and refreshes replaced takes", async () => {
    setRenderedShots([1]);
    render(<ReelWorkspace projectName="film" episode={1} />);
    await screen.findByLabelText("Episode screening");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep this draft" } });
    act(() => setRenderedShots([1, 3]));
    fireEvent.click(screen.getByRole("button", { name: "Preview shot 3" }));
    act(() => useProjectsStore.setState({ assetFingerprints: { "videos/shot3.mp4": 123 } }));
    expect(screen.getByLabelText("Episode screening")).toHaveAttribute("src", API.getFileUrl("film", "videos/shot3.mp4", 123));
    expect(screen.getByRole("textbox")).toHaveValue("Keep this draft");
    fireEvent.click(screen.getByRole("button", { name: "Shot 2" }));
    const cut = screen.getByLabelText<HTMLVideoElement>("Episode screening");
    fireEvent.loadedMetadata(cut);
    expect(cut).toHaveAttribute("src", "/media/new.mp4");
    expect(cut.currentTime).toBe(4);
  });

  it("plays a rendered take even before any stitched cut exists", async () => {
    setRenderedShots([3]);
    vi.mocked(API.listEditTimelines).mockResolvedValue({ timelines: [] });
    render(<ReelWorkspace projectName="film" episode={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview shot 3" }));
    expect(screen.getByLabelText("Episode screening")).toHaveAttribute("src", API.getFileUrl("film", "videos/shot3.mp4", null));
    expect(screen.getByRole("button", { name: "Watch stitched cut" })).toBeDisabled();
  });

  it("plays the assembled latest cut and allows comparison with the earlier cut", async () => {
    render(<ReelWorkspace projectName="film" episode={1} />);
    const video = await screen.findByLabelText("Episode screening");
    expect(video).toHaveAttribute("src", "/media/new.mp4");
    expect(screen.getByText("Review cut")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Earlier cut" }));
    await waitFor(() => expect(screen.getByLabelText("Episode screening")).toHaveAttribute("src", "/media/old.mp4"));
  });

  it("seeks a selected shot within the assembled reel and submits a scoped revision", async () => {
    render(<ReelWorkspace projectName="film" episode={1} />);
    const video = await screen.findByLabelText<HTMLVideoElement>("Episode screening");
    fireEvent.click(screen.getByRole("button", { name: "Shot 2" }));
    expect(video.currentTime).toBe(4);
    fireEvent.change(screen.getByRole("textbox", { name: "What should change?" }), { target: { value: "Keep Riven on four legs" } });
    fireEvent.click(screen.getByRole("button", { name: "Make a revised cut" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledOnce());
    const request = sendMessage.mock.calls[0][0] as string;
    expect(request).toContain("shot E1S02 and only the adjacent transitions");
    expect(request).toContain("Keep Riven on four legs");
    expect(request).toContain("Preserve the existing exported cut");
    expect(request).toContain("actual assembled export");
    expect(request).not.toContain("resume the unfinished parent production task");
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
  });

  it("retains the typed change when the director rejects the request", async () => {
    sendMessage.mockResolvedValue(false);
    render(<ReelWorkspace projectName="film" episode={1} />);
    await screen.findByLabelText("Episode screening");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep the music across this cut" } });
    fireEvent.click(screen.getByRole("button", { name: "Make a revised cut" }));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledOnce());
    expect(screen.getByRole("textbox")).toHaveValue("Keep the music across this cut");
    expect(screen.queryByText("Request sent. You can keep watching while the director works.")).not.toBeInTheDocument();
  });

  it("keeps the previous export available while the director works without claiming final quality", async () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    useProjectsStore.setState({ currentProjectData: { title: "Late Again", style: "3D", content_mode: "drama", characters: {}, episodes: [{ episode: 1, title: "Late Again", script_file: "", videos: { available: 2, total: 45, stale: 0 } }] } });
    render(<ReelWorkspace projectName="film" episode={1} />);
    expect(await screen.findByLabelText("Episode screening")).toHaveAttribute("src", "/media/new.mp4");
    expect(screen.getByText("Director is working")).toBeInTheDocument();
    expect(screen.getByText("2 of 45 videos generated")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Fix the motion" } });
    expect(screen.getByRole("button", { name: "Queue this change" })).toBeEnabled();
    expect(screen.getByText(/Review notes and any unresolved issues/)).toBeInTheDocument();
  });

  it("queues feedback during production without interrupting or duplicating an in-flight request", async () => {
    useAssistantStore.setState({ sessionStatus: "running" });
    let accept!: (accepted: boolean) => void;
    sendMessage.mockImplementation(() => new Promise<boolean>((resolve) => { accept = resolve; }));
    render(<ReelWorkspace projectName="film" episode={1} />);
    await screen.findByLabelText("Episode screening");
    fireEvent.click(screen.getByRole("button", { name: "Shot 1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Make the paper clearly held in her hand" } });
    const button = screen.getByRole("button", { name: "Queue this change" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(sendMessage).toHaveBeenCalledOnce();
    expect(sendMessage.mock.calls[0][0]).toContain("shot E1S01 and only the adjacent transitions");
    expect(sendMessage.mock.calls[0][0]).toContain("resume the unfinished parent production task");
    expect(sendMessage.mock.calls[0][0]).toContain("Respect any explicit creator pause or cancellation");
    expect(interrupt).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Make the paper clearly held in her hand");
    accept(true);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    expect(screen.getByText("Request sent. You can keep watching while the director works.")).toBeInTheDocument();
  });
});
