import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { makeNarrationSegment } from "@/test/factories";
import type { NarrationEpisodeScript, ProjectData } from "@/types";
import { TimelineCanvas } from "./TimelineCanvas";

const PROJECT: ProjectData = { title: "测试", style: "", content_mode: "narration", characters: {}, episodes: [{ episode: 1, title: "第一集", script_file: "episode_1.json" }] };

function twoShots(first = "E1S01", second = "E1S02"): NarrationEpisodeScript {
  const shot = (id: string) => makeNarrationSegment({ segment_id: id, image_prompt: `${id} 画面` });
  return { episode: 1, title: "第一集", content_mode: "narration", novel: { title: "小说", chapter: "第一章" }, segments: [shot(first), shot(second)] };
}

function board(script: NarrationEpisodeScript, props: Partial<Parameters<typeof TimelineCanvas>[0]> = {}) {
  return <LeaveGuardProvider><TimelineCanvas view="board" onViewChange={vi.fn()} projectName="demo" episode={1} projectData={PROJECT} episodeScript={script} scriptFile="episode_1.json" onUpdatePrompt={vi.fn().mockResolvedValue(true)} {...props} /></LeaveGuardProvider>;
}

describe("TimelineCanvas 定位到分镜", () => {
  it("当前分镜有修改时，通知或制作进度定位到别的分镜先询问，放行后切过去", () => {
    render(board(twoShots()));
    fireEvent.change(screen.getByDisplayValue("E1S01 画面"), { target: { value: "改到一半" } });

    act(() => useAppStore.getState().triggerScrollTo({ type: "segment", id: "E1S02" }));
    const guard = screen.getByRole("alertdialog");
    expect(screen.getByDisplayValue("改到一半")).toBeInTheDocument();

    fireEvent.click(within(guard).getByRole("button", { name: "放弃修改" }));
    expect(screen.getByDisplayValue("E1S02 画面")).toBeInTheDocument();
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("有修改时改序不询问，编辑单元与修改都留着", async () => {
    const onMoveShot = vi.fn().mockResolvedValue(true);
    const { rerender } = render(board(twoShots(), { onMoveShot }));
    fireEvent.change(screen.getByDisplayValue("E1S01 画面"), { target: { value: "改序前的修改" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "后移分镜" }));
    });
    rerender(board(twoShots("E1S02", "E1S01"), { onMoveShot }));

    expect(onMoveShot).toHaveBeenCalledWith("E1S01", "E1S02", "episode_1.json");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("改序前的修改")).toBeInTheDocument();
  });
});

/** 父级空态切换也不能卸载有修改的最后一个分镜。 */
describe("TimelineCanvas 外部删除最后一个分镜", () => {
  it("最后一镜消失时继续显示编辑器，放弃后才显示空态", () => {
    const script: NarrationEpisodeScript = { episode: 1, title: "第一集", content_mode: "narration", novel: { title: "小说", chapter: "第一章" }, segments: [makeNarrationSegment({ image_prompt: "原画面" })] };
    const page = (value: NarrationEpisodeScript) => <LeaveGuardProvider><TimelineCanvas view="board" onViewChange={vi.fn()} projectName="demo" episode={1} projectData={{ title: "测试", style: "", content_mode: "narration", characters: {}, episodes: [{ episode: 1, title: "第一集", script_file: "episode_1.json" }] }} episodeScript={value} scriptFile="episode_1.json" onUpdatePrompt={vi.fn().mockResolvedValue(true)} /></LeaveGuardProvider>;
    const { rerender } = render(page(script));
    fireEvent.change(screen.getByDisplayValue("原画面"), { target: { value: "保留最后一镜的修改" } });
    rerender(page({ ...script, segments: [] }));
    expect(screen.getByDisplayValue("保留最后一镜的修改")).toBeInTheDocument();
    expect(screen.getByText(/这个分镜已被删除/)).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.queryByDisplayValue("保留最后一镜的修改")).not.toBeInTheDocument();
    expect(screen.getByText("本集还没有分镜" )).toBeInTheDocument();
  });
});
