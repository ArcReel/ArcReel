import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { EditClip, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";

import { EditTimelineView } from "./EditTimelineView";

function summary(id: string, name: string, updatedAt: string, revision = 1): EditTimelineSummary {
  return {
    id,
    name,
    episode: 1,
    revision,
    clip_count: 3,
    created_at: "2026-09-30T08:00:00Z",
    updated_at: updatedAt,
    updated_by: { kind: "arcreel_agent", user_id: null },
    update_summary: "剪辑",
    agent_turn: null,
  };
}

function clip(overrides: Partial<EditClip> & Pick<EditClip, "id" | "unit_id" | "start" | "duration">): EditClip {
  return {
    status: "ready",
    video_version: 1,
    source_duration: 5,
    trim: null,
    source_volume: 1,
    hold: 0,
    carries_narration: false,
    narration: null,
    reason: null,
    transition_to_next: null,
    ...overrides,
  };
}

const INITIAL_CUT: EditTimelineReadout = {
  timeline: { id: "tl-00000002", name: "初剪", episode: 1 },
  revision: 3,
  latest_revision: 3,
  duration: 7.8,
  clips: [
    clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 2.8, trim: { source_in: 1.2, source_out: 4, basis_version: 1 } }),
    clip({ id: "c2", unit_id: "E1U2", start: 2.8, duration: 0, status: "unit_deleted", video_version: null, source_duration: null }),
    clip({
      id: "c3",
      unit_id: "E1U3",
      start: 2.8,
      duration: 5,
      video_version: 2,
      trim: { source_in: 0.5, source_out: 2, basis_version: 1 },
      reason: "保留推门动作",
    }),
  ],
  bgm: [],
  issues: [
    { code: "unit_deleted", severity: "info", applies_to: "all", clip_ids: ["c2"], unit_id: "E1U2", params: {} },
    {
      code: "trim_ignored",
      severity: "info",
      applies_to: "all",
      clip_ids: ["c3"],
      unit_id: "E1U3",
      params: { basis_version: 1, current_version: 2 },
    },
    { code: "unit_unused", severity: "info", applies_to: "all", clip_ids: [], unit_id: "E1U4", params: {} },
  ],
};

const SCRIPT = { episode: 1, video_units: [{ unit_id: "E1U1" }, { unit_id: "E1U3" }, { unit_id: "E1U4" }] };

function renderView() {
  return render(<EditTimelineView projectName="demo" episode={1} script={SCRIPT} aspect="16:9" />);
}

describe("EditTimelineView", () => {
  beforeEach(() => {
    useProjectsStore.setState({ projectSnapshotRevisions: {} });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  });

  it("opens the most recently edited timeline and loads the first clip with the next one preloaded", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [
        summary("tl-00000001", "按脚本顺序", "2026-09-30T09:00:00Z"),
        summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
      ],
    });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/ArcReel Agent 修改于/)).toBeInTheDocument();
    expect(read).toHaveBeenCalledWith("demo", "tl-00000002", expect.anything());
    await screen.findByTestId("edit-clip-c1");
    expect(screen.getByTestId("edit-player-video-0")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/reference_videos/E1U1.mp4?v=1",
    );
    // 已删除单元的片段被跳过，空闲的元素预载下一个可播放片段
    expect(screen.getByTestId("edit-player-video-1")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/reference_videos/E1U3.mp4?v=2",
    );
  });

  it("marks stale trims, deleted footage and unused units, and lists them as issues", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();

    const stale = await screen.findByTestId("edit-clip-c3");
    expect(stale).toHaveAttribute("data-trim-ignored", "true");
    expect(screen.queryByTestId("edit-clip-c2")).not.toBeInTheDocument();
    expect(screen.getByTestId("edit-clip-deleted-c2")).toBeInTheDocument();
    expect(screen.getByText("未使用").parentElement).toHaveTextContent("E1U4");

    const issues = screen.getByRole("heading", { name: "问题（3）" }).parentElement as HTMLElement;
    expect(within(issues).getByText("c2：素材已删除，已跳过")).toBeInTheDocument();
    expect(within(issues).getByText("视频单元 E1U4 未使用")).toBeInTheDocument();

    fireEvent.click(within(issues).getByText("c3：素材已更新，暂用完整视频"));
    const inspector = screen.getByTestId("edit-clip-inspector");
    expect(within(inspector).getByText("0.5–2s")).toHaveClass("line-through");
    expect(inspector).toHaveTextContent("（素材共 5s）");
    expect(inspector).toHaveTextContent("素材已更新，暂用完整视频");
    expect(inspector).toHaveTextContent("保留推门动作");
  });

  it("shows the new revision after the project reports a change", async () => {
    const list = vi
      .spyOn(API, "listEditTimelines")
      .mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    list.mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:05:00Z", 4)] });
    read.mockResolvedValue({
      ...INITIAL_CUT,
      revision: 4,
      latest_revision: 4,
      duration: 4.8,
      clips: [INITIAL_CUT.clips[0], clip({ id: "c4", unit_id: "E1U4", start: 2.8, duration: 2 })],
      issues: [],
    });
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    expect(await screen.findByTestId("edit-clip-c4")).toBeInTheDocument();
    expect(screen.queryByTestId("edit-clip-c3")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "问题（0）" })).toBeInTheDocument();
  });

  it("keeps the last preview when a refresh fails", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    const read = vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    read.mockRejectedValue(new Error("probe failed"));
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(screen.getByTestId("edit-clip-c3")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the timeline being watched when another one is created", async () => {
    const list = vi
      .spyOn(API, "listEditTimelines")
      .mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    renderView();
    await screen.findByTestId("edit-clip-c3");

    list.mockResolvedValue({
      timelines: [
        summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3),
        summary("tl-00000003", "快节奏版", "2026-09-30T11:00:00Z"),
      ],
    });
    act(() => useProjectsStore.setState({ projectSnapshotRevisions: { demo: 1 } }));

    expect(await screen.findByRole("tab", { name: "快节奏版" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
  });

  it("hands the current timeline and its issues to the actions area", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({
      timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)],
    });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);
    Element.prototype.scrollIntoView = vi.fn();

    render(
      <EditTimelineView
        projectName="demo"
        episode={1}
        script={SCRIPT}
        aspect="16:9"
        renderActions={({ timelineId, timelineName, issues, showIssues }) => (
          <button type="button" onClick={showIssues}>
            {`${timelineName} ${timelineId} ${issues === null ? "…" : issues.length}`}
          </button>
        )}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "初剪 tl-00000002 3" }));
    expect(screen.getByRole("heading", { name: "问题（3）" })).toHaveFocus();
  });

  it("lets the empty state reload the list after a timeline is created", async () => {
    const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });
    vi.spyOn(API, "getEditTimeline").mockResolvedValue(INITIAL_CUT);

    render(
      <EditTimelineView
        projectName="demo"
        episode={1}
        script={SCRIPT}
        aspect="16:9"
        renderEmptyState={({ reload }) => (
          <button type="button" onClick={reload}>
            新建
          </button>
        )}
      />,
    );

    list.mockResolvedValue({ timelines: [summary("tl-00000002", "初剪", "2026-09-30T10:00:00Z", 3)] });
    fireEvent.click(await screen.findByRole("button", { name: "新建" }));

    expect(await screen.findByRole("tab", { name: "初剪" })).toHaveAttribute("aria-selected", "true");
  });

  it("explains the empty state when the episode has no edit timeline", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });

    renderView();

    expect(await screen.findByText("还没有剪辑时间线")).toBeInTheDocument();
  });
});
