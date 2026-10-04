import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { makePlan, makeStatus } from "@/test/factories";
import type { EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import { EditTimelineEmptyState } from "./EditTimelineEmptyState";

/** 制作状态里本集「新建剪辑时间线」的准入不成立：还没有可用视频。 */
function refuseCreate(projectName: string, episode: number) {
  const status = makeStatus({ operations: { create_edit_timeline: { state: "refused", reason: "no_available_video" } } });
  useWorkflowStore.setState({ plan: makePlan({ status }), planKey: `${projectName}::${episode}` });
}

const PREFILL = "为《雨夜》（集 ID 3）剪辑成片";

function created(name: string): EditTimelineReadout {
  return {
    timeline: { id: "tl-1", name, episode: 2 },
    revision: 1,
    latest_revision: 1,
    duration: 0,
    clips: [],
    bgm: [],
    issues: [],
  };
}

function summary(name: string): EditTimelineSummary {
  return {
    id: "tl-0",
    name,
    episode: 2,
    revision: 1,
    clip_count: 0,
    created_at: "",
    updated_at: "",
    updated_by: { kind: "creator", user_id: null },
    update_summary: "",
    agent_turn: null,
  };
}

describe("EditTimelineEmptyState", () => {
  beforeEach(() => {
    useWorkflowStore.setState({ plan: null, planKey: null });
    useAssistantStore.setState({ input: "" });
    useAppStore.setState({ assistantPanelOpen: false, toast: null });
    useProjectsStore.setState({
      currentProjectData: { episodes: [{ episode: 1 }, { episode: 3, title: "雨夜" }] } as never,
    });
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("按脚本新建剪辑时间线并刷新项目，不打开 Agent 面板", async () => {
    const result = created("完整版");
    const create = vi.spyOn(API, "createEditTimeline").mockResolvedValue(result);
    const onCreated = vi.fn();

    render(<EditTimelineEmptyState projectName="demo" episode={2} onCreated={onCreated} />);
    await userEvent.click(screen.getByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(result));
    expect(create).toHaveBeenCalledWith("demo", 2, "完整版");
    expect(useProjectsStore.getState().refreshProject).toHaveBeenCalledWith("demo");
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
    // 新建的剪辑时间线随即成为选中的标签，不另弹提示
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("显示名与「剪辑」行同一规则：集内已有「完整版」时加序号", async () => {
    vi.mocked(API.listEditTimelines).mockResolvedValue({ timelines: [summary("完整版")] });
    const create = vi.spyOn(API, "createEditTimeline").mockResolvedValue(created("完整版 2"));

    render(<EditTimelineEmptyState projectName="demo" episode={2} onCreated={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith("demo", 2, "完整版 2"));
  });

  it("交给 Agent 剪辑只预填请求并打开面板", async () => {
    const create = vi.spyOn(API, "createEditTimeline");

    render(<EditTimelineEmptyState projectName="demo" episode={3} onCreated={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "交给 Agent 剪辑" }));

    // 集 ID 3 在账本里排第 2：预填按集名称呼并附集 ID，不把集 ID 当第几集
    expect(useAssistantStore.getState().input).toBe(PREFILL);
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    expect(create).not.toHaveBeenCalled();
  });

  it("制作状态拒绝新建剪辑时间线时两个按钮都不可点，并说明需要先生成视频", () => {
    refuseCreate("demo", 1);
    render(<EditTimelineEmptyState projectName="demo" episode={1} onCreated={vi.fn()} />);

    expect(screen.getByRole("button", { name: "交给 Agent 剪辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建剪辑时间线" })).toBeDisabled();
    expect(screen.getByText("需要先生成视频")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建剪辑时间线" }).parentElement).toHaveAttribute(
      "title",
      "需要先生成视频",
    );
  });

  it("制作状态属于别的集时不采用它的准入", () => {
    refuseCreate("demo", 2);
    render(<EditTimelineEmptyState projectName="demo" episode={1} onCreated={vi.fn()} />);

    expect(screen.getByRole("button", { name: "新建剪辑时间线" })).toBeEnabled();
  });

  it("新建失败时提示原因", async () => {
    vi.spyOn(API, "createEditTimeline").mockRejectedValue(new Error("集（id=1）已有名为「完整版」的剪辑时间线"));
    const onCreated = vi.fn();

    render(<EditTimelineEmptyState projectName="demo" episode={1} onCreated={onCreated} />);
    await userEvent.click(screen.getByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe(
        "新建剪辑时间线失败：集（id=1）已有名为「完整版」的剪辑时间线",
      ),
    );
    expect(onCreated).not.toHaveBeenCalled();
  });
});
