import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import { EpisodePage } from "./EpisodePage";

vi.mock("@/components/workflow/WorkflowPanel", () => ({ WorkflowPanel: () => null }));

const AD_PROJECT: ProjectData = {
  title: "夏日汽水", style: "", content_mode: "ad", characters: {}, generation_mode: "storyboard",
  target_duration: 30,
  overview: { synopsis: "0-3 秒：冰块落进杯子", genre: "饮品广告", theme: "清凉", world_setting: "海边小店" },
  episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
};

function page() {
  return (
    <LeaveGuardProvider>
      <EpisodePage projectName="ad" episode={1} projectData={AD_PROJECT} script={null} demo={false}
        onSaveTitle={vi.fn()} onViewUnit={vi.fn()}
        renderCanvas={({ view }) => <p>画布：{view}</p>} />
    </LeaveGuardProvider>
  );
}

describe("EpisodePage 广告项目的「故事设定」视图", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState({ currentProjectName: "ad", currentProjectData: AD_PROJECT });
    vi.spyOn(API, "getProject").mockResolvedValue({ project: AD_PROJECT, scripts: {} });
    window.history.replaceState(null, "", "/?view=setting");
  });

  it("排在 tab 组第一个，替换画布，且没有原文可生成", () => {
    render(page());

    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs[0]).toBe("故事设定");
    expect(screen.queryByText(/^画布：/)).not.toBeInTheDocument();
    const setting = screen.getByRole("region", { name: "故事设定" });
    expect(within(setting).getByRole("textbox", { name: "梗概" })).toHaveValue("0-3 秒：冰块落进杯子");
    expect(within(setting).queryByRole("button", { name: /从原文/ })).not.toBeInTheDocument();
  });

  it("有未保存修改时切到别的视图先询问，保存提交四个字段", async () => {
    const update = vi.spyOn(API, "updateOverview").mockResolvedValue({ success: true } as never);
    render(page());
    const setting = screen.getByRole("region", { name: "故事设定" });
    fireEvent.change(within(setting).getByRole("textbox", { name: "主题" }), { target: { value: "解暑" } });

    fireEvent.click(screen.getByRole("tab", { name: "分镜" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "继续编辑" }));
    expect(screen.queryByText(/^画布：/)).not.toBeInTheDocument();

    fireEvent.click(within(setting).getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("ad", {
        synopsis: "0-3 秒：冰块落进杯子", genre: "饮品广告", theme: "解暑", world_setting: "海边小店",
      }),
    );
  });
});
