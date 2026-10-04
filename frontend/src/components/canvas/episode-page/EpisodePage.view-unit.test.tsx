import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LeaveGuardProvider, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeScript, ProjectData } from "@/types";
import { makeNarrationSegment } from "@/test/factories";
import { EpisodePage } from "./EpisodePage";

vi.mock("@/components/workflow/WorkflowPanel", () => ({
  WorkflowPanel: ({ onViewUnit }: { onViewUnit?: (unitId: string) => void }) => (
    <button type="button" onClick={() => onViewUnit?.("E1S02")}>查看 E1S02</button>
  ),
}));

const PROJECT: ProjectData = {
  title: "测试", style: "", content_mode: "narration", characters: {},
  generation_mode: "storyboard", grid_storyboard: true,
  episodes: [{ episode: 1, title: "第一集", script_file: "episode_1.json" }],
};
const SCRIPT: EpisodeScript = {
  episode: 1, title: "第一集", content_mode: "narration", novel: { title: "小说", chapter: "第一章" },
  segments: [makeNarrationSegment({ segment_id: "E1S01" }), makeNarrationSegment({ segment_id: "E1S02" })],
};

const saveNothing = async () => true;
/** 宫格视图里带着未保存修改的编辑单元，切到分镜视图时会卸载。 */
function DirtyGridUnit() {
  useLeaveGuard({ dirty: true, save: saveNothing });
  return null;
}

function page(onViewUnit: (unitId: string) => void, dirtyOnGrid = false) {
  return (
    <LeaveGuardProvider>
      <EpisodePage projectName="demo" episode={1} projectData={PROJECT} script={SCRIPT} demo={false}
        onSaveTitle={vi.fn()} onViewUnit={onViewUnit}
        renderCanvas={({ view }) => <><p>当前视图：{view}</p>{dirtyOnGrid && view === "grid" && <DirtyGridUnit />}</>} />
    </LeaveGuardProvider>
  );
}

describe("EpisodePage 制作进度里的「查看」", () => {
  beforeEach(() => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
    window.history.replaceState(null, "", "/?view=grid");
  });

  it("不在分镜视图时先切到分镜视图，再定位到单元", () => {
    const onViewUnit = vi.fn();
    render(page(onViewUnit));
    expect(screen.getByText("当前视图：grid")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看 E1S02" }));

    expect(onViewUnit).toHaveBeenCalledWith("E1S02");
    expect(screen.getByText("当前视图：board")).toBeInTheDocument();
  });

  it("切走会卸载有修改的编辑单元时只询问一次，继续编辑则视图与定位都不变", () => {
    const onViewUnit = vi.fn();
    render(page(onViewUnit, true));

    fireEvent.click(screen.getByRole("button", { name: "查看 E1S02" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "继续编辑" }));
    expect(onViewUnit).not.toHaveBeenCalled();
    expect(screen.getByText("当前视图：grid")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看 E1S02" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "放弃修改" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onViewUnit).toHaveBeenCalledWith("E1S02");
    expect(screen.getByText("当前视图：board")).toBeInTheDocument();
  });
});
