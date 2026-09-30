import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { ApiRequestError } from "@/api/errors";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, ProjectData, ProjectStatus } from "@/types/project";
import type { WorkflowNextAction, WorkflowStatus } from "@/types/workflow";

import { ProjectStatusBar } from "./ProjectStatusBar";

const SUMMARY: ProjectStatus = {
  needs_repair: false,
  repair_reason: null,
  assets: {},
  episodes_summary: { total: 3, scripted: 2, in_production: 1, completed: 1 },
};

const EPISODES: Partial<EpisodeMeta>[] = [
  { episode: 4, title: "旧账", status: "completed" },
  { episode: 7, title: "雨夜", status: "in_production", videos: { total: 2, available: 1, stale: 1 } },
  { episode: 2, title: "", status: "draft" },
];

function action(type: WorkflowNextAction["type"], args: Record<string, unknown> = {}): WorkflowNextAction {
  return { type, args, requested_ids: [], requires_confirmation: false, reason: "" };
}

function workflowStatus(
  next: WorkflowNextAction,
  overrides: Partial<WorkflowStatus> = {},
): WorkflowStatus {
  return {
    schema_version: 2,
    project_revision: "r",
    source_revision: null,
    project: { content_mode: "narration", generation_mode: "storyboard", grid_storyboard: false },
    target: null,
    blockers: [],
    issues: [],
    content: null,
    operations: {},
    gates: {},
    artifacts: {},
    next_action: next,
    next_alternatives: [],
    ...overrides,
  };
}

function setProject(status: ProjectStatus = SUMMARY, episodes = EPISODES, contentMode = "narration") {
  useProjectsStore.setState({
    currentProjectName: "demo",
    currentProjectData: { content_mode: contentMode, status, episodes } as unknown as ProjectData,
  });
}

function renderBar(path = "/") {
  const location = memoryLocation({ path, record: true });
  render(
    <Router hook={location.hook}>
      <ProjectStatusBar projectName="demo" />
    </Router>,
  );
  return location;
}

describe("ProjectStatusBar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState({ input: "", sending: false });
  });

  it("shows episode progress and continues the first unfinished episode", async () => {
    setProject();
    vi.spyOn(API, "getWorkflowStatus").mockResolvedValue(
      workflowStatus(action("author_prompts", { episode_id: 7 })),
    );
    const location = renderBar();
    const user = userEvent.setup();

    expect(screen.getByText("1/3 集")).toBeInTheDocument();
    expect(screen.getByLabelText("1 集有产物需要更新")).toHaveTextContent("1");
    await user.click(await screen.findByRole("button", { name: /继续第 2 集/ }));

    expect(screen.getByText("「雨夜」的下一步：补充提示词。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "去第 2 集" }));
    expect(location.history?.at(-1)).toBe("/episodes/7");
  });

  it("yields to the episode panel when the next step is the current episode", async () => {
    setProject();
    vi.spyOn(API, "getWorkflowStatus").mockResolvedValue(
      workflowStatus(action("author_prompts", { episode_id: 7 })),
    );
    renderBar("/episodes/7");

    expect(await screen.findByText("下一步见本集面板")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /继续第 2 集/ })).not.toBeInTheDocument();
  });

  it("lists every episode with its state or next step and jumps to it", async () => {
    setProject();
    vi.spyOn(API, "getWorkflowStatus").mockResolvedValue(workflowStatus(action("none")));
    vi.spyOn(API, "getEpisodeNextSteps").mockResolvedValue({
      episodes: [
        { episode: 4, plan_stale: false, next_action: action("none") },
        { episode: 7, plan_stale: false, next_action: action("generate_videos", { episode_id: 7 }) },
        { episode: 2, plan_stale: true, next_action: action("none") },
      ],
    });
    const location = renderBar();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /1\/3 集/ }));

    const rows = await screen.findAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "第 1 集旧账已完成",
      "第 2 集雨夜下一步：生成缺失的视频",
      "第 3 集第 3 集原文已重新规划",
    ]);
    await user.click(within(rows[1]).getByRole("button"));
    expect(location.history?.at(-1)).toBe("/episodes/7");
  });

  it("hands episode planning to the agent with the additional requirements", async () => {
    setProject({ ...SUMMARY, episodes_summary: { total: 0, scripted: 0, in_production: 0, completed: 0 } }, []);
    vi.spyOn(API, "getWorkflowStatus").mockResolvedValue(
      workflowStatus(action("plan_episodes"), {
        next_alternatives: [action("create_episode")],
      }),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderBar();
    const user = userEvent.setup();

    expect(screen.getByText("尚未建集")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /AI 分集规划/ }));
    await user.type(screen.getByRole("textbox"), "每集 90 秒");
    await user.click(screen.getByRole("button", { name: "交给 Agent" }));

    expect(useAssistantStore.getState().input).toBe(
      "请为整本源文规划分集，一直规划到源文结尾。\n附加要求：每集 90 秒",
    );
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    expect(screen.getByRole("button", { name: "交给 Agent 新建一集" })).toBeInTheDocument();
    // 只预填不发送
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows short-film progress without an episode list for ad projects", () => {
    setProject(
      { ...SUMMARY, episodes_summary: { total: 1, scripted: 1, in_production: 0, completed: 0 } },
      [{ episode: 1, title: "", status: "scripted" }],
      "ad",
    );
    vi.spyOn(API, "getWorkflowStatus").mockReturnValue(new Promise(() => {}));
    renderBar();

    expect(screen.getByRole("button", { name: /短片未完成/ })).toBeDisabled();
  });

  it("retries a failed migration in place, reporting the reason until it succeeds", async () => {
    setProject({ ...SUMMARY, needs_repair: true, repair_reason: "step 0014 failed" });
    const getStatus = vi.spyOn(API, "getWorkflowStatus").mockReturnValue(new Promise(() => {}));
    vi
      .spyOn(API, "retryProjectMigration")
      .mockRejectedValueOnce(
        new ApiRequestError("数据升级仍未完成", { reason: "scripts/episode_4.json 读取失败", details: [] }, 422),
      )
      .mockResolvedValueOnce({ success: true });
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: { content_mode: "narration", status: SUMMARY, episodes: EPISODES } as unknown as ProjectData,
      scripts: {},
    });
    renderBar();
    const user = userEvent.setup();

    expect(screen.getByText("数据升级没有完成")).toBeInTheDocument();
    expect(getStatus).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("重试没有成功 · 已重试 1 次")).toBeInTheDocument();
    expect(screen.getByText("scripts/episode_4.json 读取失败")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "交给 Agent 排查" }));
    expect(useAssistantStore.getState().input).toContain("retry_project_migration");

    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("1/3 集")).toBeInTheDocument();
    expect(screen.queryByText("数据升级没有完成")).not.toBeInTheDocument();
    expect(useAppStore.getState().toast?.text).toBe("数据升级已完成，生成功能已恢复。");
  });
});
