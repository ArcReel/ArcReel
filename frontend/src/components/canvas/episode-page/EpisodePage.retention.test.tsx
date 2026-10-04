import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeScript, ProjectData } from "@/types";
import { makeNarrationSegment } from "@/test/factories";
import { TimelineCanvas } from "@/components/canvas/timeline/TimelineCanvas";
import { EpisodePage } from "./EpisodePage";

vi.mock("@/components/workflow/WorkflowPanel", () => ({ WorkflowPanel: () => null }));

describe("EpisodePage 外部替换编辑器", () => {
  it("原文编辑中 Agent 生成正式脚本时保留可见原文，放弃后进入分镜", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("集原文");
    const project: ProjectData = {
      title: "测试", style: "", content_mode: "narration", characters: {},
      episodes: [{ episode: 1, title: "第一集", script_file: "", source_origin: "own" }],
    };
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project });
    const page = (script: EpisodeScript | null) => (
      <LeaveGuardProvider>
        <EpisodePage projectName="demo" episode={1} projectData={project} script={script} demo={false}
          onSaveTitle={vi.fn()} onViewUnit={vi.fn()} renderCanvas={() => <p>正式分镜画布</p>} />
      </LeaveGuardProvider>
    );
    const { rerender } = render(page(null));
    fireEvent.change(await screen.findByRole("textbox", { name: "本集原文" }), { target: { value: "正在改的原文" } });
    rerender(page({ episode: 1, title: "第一集", novel: { title: "测试", chapter: "第一章" }, content_mode: "narration", segments: [] }));
    expect(screen.getByRole("textbox", { name: "本集原文" })).toHaveValue("正在改的原文");
    expect(screen.getByText(/本集已有新的脚本内容/)).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByText("正式分镜画布")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByText("正式分镜画布")).toBeInTheDocument();
  });
  it("删除当前集：确认删除后才处理修改，用户放行之前不提交删除", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("集原文");
    const remove = vi.spyOn(API, "deleteEpisode").mockImplementation(async (_project, episode, revision) =>
      revision
        ? { status: "deleted", impact: { episode, recoverable: false, revision } }
        : { status: "confirmation_required", impact: { episode, recoverable: false, revision: "rev-1", text: "集原文会一并删除" } },
    );
    const project: ProjectData = { title: "测试", style: "", content_mode: "narration", characters: {}, episodes: [{ episode: 1, title: "第一集", script_file: "", source_origin: "own" }] };
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project });
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    render(<LeaveGuardProvider><EpisodePage projectName="demo" episode={1} projectData={project} script={null} demo={false} onSaveTitle={vi.fn()} onViewUnit={vi.fn()} renderCanvas={() => null} /></LeaveGuardProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "本集原文" }), { target: { value: "未保存原文" } });
    await userEvent.click(screen.getByRole("button", { name: "这一集的更多操作" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "删除这一集" }));
    // 不带 revision 只取丢失清单，服务端不删除
    const deletion = await screen.findByRole("alertdialog");
    expect(deletion).toHaveTextContent("集原文会一并删除");
    expect(remove).toHaveBeenCalledWith("demo", 1);

    fireEvent.click(within(deletion).getByRole("button", { name: "删除" }));
    const guard = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    expect(remove).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue("未保存原文")).toHaveValue("未保存原文");
    fireEvent.click(within(guard).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("demo", 1, "rev-1"));
  });

  it.each(["script", "episode"])("外部移除 %s 时保留可见分镜，放弃后才离开", async (removed) => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("集原文");
    const project: ProjectData = { title: "测试", style: "", content_mode: "narration", characters: {}, episodes: [{ episode: 1, title: "第一集", script_file: "episode_1.json", source_origin: "own" }] };
    const script: EpisodeScript = { episode: 1, title: "第一集", novel: { title: "小说", chapter: "第一章" }, content_mode: "narration", segments: [makeNarrationSegment({ image_prompt: "正式分镜画面" })] };
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project });
    const page = (data: ProjectData, value: EpisodeScript | null) => <LeaveGuardProvider><EpisodePage projectName="demo" episode={1} projectData={data} script={value} demo={false} onSaveTitle={vi.fn()} onViewUnit={vi.fn()} renderCanvas={(context) => <TimelineCanvas {...context} projectName="demo" episode={1} projectData={data} episodeScript={value} scriptFile="episode_1.json" onUpdatePrompt={vi.fn().mockResolvedValue(true)} />} /></LeaveGuardProvider>;
    const { rerender } = render(page(project, script));
    fireEvent.change(screen.getByDisplayValue("正式分镜画面"), { target: { value: "不能丢失的分镜修改" } });
    rerender(page(removed === "episode" ? { ...project, episodes: [] } : project, null));
    expect(screen.getByDisplayValue("不能丢失的分镜修改")).toBeInTheDocument();
    expect(screen.getByText(/本集(脚本已被移除|已被删除)/)).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.queryByDisplayValue("不能丢失的分镜修改")).not.toBeInTheDocument();
  });

});
