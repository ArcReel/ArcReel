import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { stubMediaElementPlayback } from "@/test/media-element";
import type { EditClip, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { ReelWorkspace } from "./ReelWorkspace";

const { sendMessage } = vi.hoisted(() => ({ sendMessage: vi.fn() }));
vi.mock("@/hooks/useAssistantSession", () => ({ useAssistantSession: () => ({ sendMessage, answerQuestion: vi.fn(), interrupt: vi.fn() }) }));
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
    useAssistantStore.setState({ entries: [], sending: false, sessionStatus: "idle", pendingQuestion: null, error: null });
    useProjectsStore.setState({ currentScripts: {}, currentProjectData: { title: "Late Again", style: "3D", content_mode: "drama", characters: {}, episodes: [{ episode: 1, title: "Late Again", script_file: "" }] } });
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [summary("old", "Earlier cut", "2026-10-01"), summary("new", "Revised cut", "2026-10-02")] });
    vi.spyOn(API, "getEditTimeline").mockImplementation(async (_project, id) => timeline(id));
    vi.spyOn(API, "getFinalCutStatus").mockImplementation(async (_project, id) => ({ episode: 1, timeline_id: id, status: "current", artifact_path: `${id}.mp4`, version: 1, rendered_at: null, narration: "without_narration", subtitles: "no_subtitles", download_url: `/media/${id}.mp4` }));
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
    expect(screen.getByRole("button", { name: "Make a revised cut" })).toBeDisabled();
    expect(screen.getByText(/Review notes and any unresolved issues/)).toBeInTheDocument();
  });
});
