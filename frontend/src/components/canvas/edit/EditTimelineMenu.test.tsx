import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { stubMediaElementPlayback } from "@/test/media-element";

import { EditTimelineView } from "./EditTimelineView";

function summary(id: string, name: string, updatedAt: string): EditTimelineSummary {
  return {
    id,
    name,
    episode: 1,
    revision: 1,
    clip_count: 0,
    created_at: "2026-09-30T08:00:00Z",
    updated_at: updatedAt,
    updated_by: { kind: "arcreel_agent", user_id: null },
    update_summary: "剪辑",
    agent_turn: null,
  };
}

const FIRST = summary("tl-00000001", "完整版", "2026-09-30T09:00:00Z");
const SECOND = summary("tl-00000002", "快节奏版", "2026-09-30T10:00:00Z");

function readoutOf(id: string, name: string): EditTimelineReadout {
  return {
    timeline: { id, name, episode: 1 },
    revision: 1,
    latest_revision: 1,
    duration: 0,
    clips: [],
    bgm: [],
    issues: [],
  };
}

function renderView() {
  return render(
    <EditTimelineView
      projectName="demo"
      episode={1}
      script={{ episode: 1, video_units: [] }}
      aspect="16:9"
      ttsNarration
      renderEmptyState={() => <p>no timeline</p>}
    />,
  );
}

async function openMenu(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: `「${name}」的更多操作` }));
  return screen.findByRole("menu");
}

describe("EditTimelineView tab menu", () => {
  beforeEach(() => {
    useProjectsStore.setState({ projectSnapshotRevisions: {} });
    useAppStore.setState({ toast: null });
    stubMediaElementPlayback();
    vi.spyOn(API, "getEditTimeline").mockImplementation((_project, id) =>
      Promise.resolve(readoutOf(id, id === FIRST.id ? FIRST.name : SECOND.name)),
    );
  });

  it("offers rename and delete for the selected timeline", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });

    renderView();
    const menu = await openMenu("快节奏版");

    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["重命名", "删除"]);
  });

  it("renames the timeline and shows the new name after reloading the list", async () => {
    const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    const rename = vi
      .spyOn(API, "renameEditTimeline")
      .mockResolvedValue({ ...SECOND, name: "定稿" });

    renderView();
    const menu = await openMenu("快节奏版");
    fireEvent.click(within(menu).getByRole("menuitem", { name: "重命名" }));
    const input = await screen.findByRole("textbox", { name: "显示名" });
    expect(input).toHaveValue("快节奏版");
    fireEvent.change(input, { target: { value: "  定稿 " } });
    list.mockResolvedValue({ timelines: [FIRST, { ...SECOND, name: "定稿" }] });
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));

    await waitFor(() => expect(rename).toHaveBeenCalledWith("demo", "tl-00000002", "定稿"));
    expect(await screen.findByRole("tab", { name: "定稿" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "快节奏版" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "显示名" })).not.toBeInTheDocument();
  });

  it("can rename the same timeline again right after a successful rename", async () => {
    const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    vi.spyOn(API, "renameEditTimeline").mockResolvedValue({ ...SECOND, name: "定稿" });

    renderView();
    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "重命名" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "显示名" }), { target: { value: "定稿" } });
    list.mockResolvedValue({ timelines: [FIRST, { ...SECOND, name: "定稿" }] });
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    await screen.findByRole("tab", { name: "定稿" });

    fireEvent.click(within(await openMenu("定稿")).getByRole("menuitem", { name: "重命名" }));
    expect(await screen.findByRole("textbox", { name: "显示名" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });

  it("keeps the rename dialog open and explains a failure", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    vi.spyOn(API, "renameEditTimeline").mockRejectedValue(new Error("第 1 集已有名为「完整版」的剪辑时间线"));

    renderView();
    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "重命名" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "显示名" }), { target: { value: "完整版" } });
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("重命名失败：第 1 集已有名为「完整版」的剪辑时间线");
    expect(screen.getByRole("textbox", { name: "显示名" })).toBeInTheDocument();
  });

  it("does not rename to a blank name", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    const rename = vi.spyOn(API, "renameEditTimeline");

    renderView();
    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "重命名" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "显示名" }), { target: { value: "   " } });

    expect(screen.getByRole("button", { name: "重命名" })).toBeDisabled();
    expect(rename).not.toHaveBeenCalled();
  });

  it("asks for confirmation before deleting and falls back to the other timeline", async () => {
    const list = vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    const remove = vi.spyOn(API, "deleteEditTimeline").mockResolvedValue();

    renderView();
    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "删除" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("删除剪辑时间线「快节奏版」？");
    expect(dialog).toHaveTextContent("删除后无法恢复");
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "删除" }));
    list.mockResolvedValue({ timelines: [FIRST] });
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("demo", "tl-00000002"));
    await waitFor(() => expect(screen.queryByRole("tab", { name: "快节奏版" })).not.toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "完整版" })).toHaveAttribute("aria-selected", "true");
    // 删除成功只以标签消失为反馈，不弹提示
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("explains in the dialog why a delete was refused and keeps the timeline", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [FIRST, SECOND] });
    vi.spyOn(API, "deleteEditTimeline").mockRejectedValue(new Error("这条剪辑时间线有渲染任务在排队或执行"));

    renderView();
    fireEvent.click(within(await openMenu("快节奏版")).getByRole("menuitem", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("删除失败：这条剪辑时间线有渲染任务在排队或执行");
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "快节奏版" })).toBeInTheDocument();
  });
});
