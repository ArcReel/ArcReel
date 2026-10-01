import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useScriptPlanStore } from "@/stores/script-plan-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { WorkflowPanel } from "./WorkflowPanel";
import { makeContent, makePlan, makeStatus, makeStep, makeTask } from "@/test/factories";
import type { ProjectData } from "@/types";
import type {
  WorkflowActionType,
  WorkflowContent,
  WorkflowNextAction,
  WorkflowPlan,
  WorkflowPlanStep,
  WorkflowStatus,
} from "@/types/workflow";

function nextAction(type: WorkflowActionType, overrides: Partial<WorkflowNextAction> = {}): WorkflowNextAction {
  return { type, args: { episode_id: 1 }, requested_ids: [], requires_confirmation: false, reason: "next", ...overrides };
}

interface Scenario {
  next: WorkflowNextAction;
  content?: Partial<WorkflowContent>;
  status?: Partial<WorkflowStatus>;
  steps?: WorkflowPlanStep[];
}

/** 一集的制作状态场景：计划的下一步与状态的下一步同一个。 */
function scenario({ next, content, status, steps }: Scenario): WorkflowPlan {
  const built = makeStatus({ content: makeContent(content), next_action: next, ...status });
  return makePlan({ status: built, next_action: next, next_alternatives: built.next_alternatives, steps: steps ?? [] });
}

function mockPlan(plan: WorkflowPlan) {
  return vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(plan);
}

async function renderPanel(plan: WorkflowPlan, props: Partial<React.ComponentProps<typeof WorkflowPanel>> = {}) {
  mockPlan(plan);
  render(<WorkflowPanel projectName="proj" episode={1} {...props} />);
  return screen.findByRole("button", { name: /制作进度/ });
}

/** 面板默认收起；逐行现状要先展开。 */
async function renderExpanded(plan: WorkflowPlan, props: Partial<React.ComponentProps<typeof WorkflowPanel>> = {}) {
  const toggle = await renderPanel(plan, props);
  fireEvent.click(toggle);
  return toggle;
}

beforeEach(() => {
  useWorkflowStore.getState().resetTarget();
  useAssistantStore.getState().setInput("");
  useProjectsStore.setState({ currentProjectData: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WorkflowPanel 收起行", () => {
  it("收起时一行给出现状与下一步的主次入口，不展开步骤行", async () => {
    const toggle = await renderPanel(
      scenario({ next: nextAction("author_prompts"), content: { pending_authoring_ids: ["E1S02"] } }),
      { onAuthorPrompts: vi.fn() },
    );
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByText("提示词：共 2 个，1 个待编写")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "交给 Agent" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 编写" })).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-row-prompts")).not.toBeInTheDocument();
  });

  it("本集完成时收起行说明已完成，不给入口", async () => {
    await renderPanel(
      scenario({
        next: nextAction("none", { args: {} }),
        steps: [makeStep({ id: "edit", state: "completed" })],
      }),
    );
    expect(await screen.findByText("本集已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "交给 Agent" })).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 逐行现状", () => {
  it("每行只陈述自己那类内容，前面没做完时后面写「还没有」", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("prepare_script_plan"),
        content: { formal_script: "absent", script_item_count: null },
        status: { artifacts: { script_plan: { state: "missing" } } },
      }),
    );
    expect(within(screen.getByTestId("workflow-row-source")).getByText("有集原文")).toBeInTheDocument();
    for (const key of ["script", "prompts", "assets", "boards", "videos", "edit"]) {
      expect(within(screen.getByTestId(`workflow-row-${key}`)).getByText("还没有")).toBeInTheDocument();
    }
  });

  it("下一步就地展开在所属行，其他行不带下一步", async () => {
    await renderExpanded(
      scenario({ next: nextAction("author_prompts"), content: { pending_authoring_ids: ["E1S02"] } }),
      { onAuthorPrompts: vi.fn() },
    );
    const prompts = screen.getByTestId("workflow-row-prompts");
    expect(prompts).toHaveAttribute("aria-current", "step");
    expect(within(prompts).getByTestId("workflow-next-step")).toBeInTheDocument();
    expect(screen.getAllByTestId("workflow-next-step")).toHaveLength(1);
  });

  it("广告的第一行是创作灵感与商品，标题行给出总时长 / 目标时长，超出 10% 以上时标出", async () => {
    useProjectsStore.setState({
      currentProjectData: {
        content_mode: "ad",
        target_duration: 30,
        episodes: [{ episode: 1, title: "夏日冰饮", script_file: "scripts/episode_1.json", duration_seconds: 38 }],
      } as unknown as ProjectData,
    });
    await renderPanel(
      scenario({
        next: nextAction("generate_videos"),
        content: { ad_inputs: "present", episode_source: "not_applicable" },
        status: { project: { content_mode: "ad", generation_mode: "reference_video", grid_storyboard: false } },
      }),
    );
    const badge = await screen.findByText("总时长 38 秒 / 目标 30 秒");
    expect(badge).toHaveAttribute("data-over", "true");
    fireEvent.click(screen.getByRole("button", { name: /制作进度/ }));
    expect(screen.getAllByRole("listitem")[0]).toHaveAttribute("data-testid", "workflow-row-brief");
  });
});

describe("WorkflowPanel 准入与置灰", () => {
  it("广告没有灵感和商品时入口照常显示但不可点，悬停说明原因，并给出跳转提示", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("collect_project_input", { args: {} }),
        content: { ad_inputs: "absent", episode_source: "not_applicable", formal_script: "absent", script_item_count: null },
        status: {
          project: { content_mode: "ad", generation_mode: "storyboard", grid_storyboard: false },
          operations: { generate_script: { state: "refused", reason: "ad_brief_and_products_missing" } },
        },
      }),
    );
    const next = screen.getByTestId("workflow-next-step");
    const agent = within(next).getByRole("button", { name: "交给 Agent" });
    expect(agent).toHaveAttribute("aria-disabled", "true");
    expect(agent).toHaveAttribute("title", "需要先填写创作灵感或添加商品");
    expect(agent).toHaveAccessibleDescription("需要先填写创作灵感或添加商品");

    fireEvent.click(agent);
    expect(useAssistantStore.getState().input).toBe("");
    expect(within(next).getByRole("button", { name: "去填写" })).toBeInTheDocument();
  });

  it("没有集原文时脚本规划行的规划入口置灰，悬停说明需要先补充集原文", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("start_blank_script"),
        content: { episode_source: "absent", formal_script: "absent", script_item_count: null },
        status: {
          artifacts: { script_plan: { state: "missing" } },
          operations: { prepare_script_plan: { state: "refused", reason: "episode_source_missing" } },
        },
      }),
    );
    const plan = screen.getByTestId("workflow-row-plan");
    const entry = within(plan).getByRole("button", { name: "交给 Agent 规划脚本" });
    expect(entry).toHaveAttribute("aria-disabled", "true");
    expect(entry).toHaveAttribute("title", "需要先补充集原文");
    // 下一步挂在正式脚本行，给的是从空白开始
    expect(within(screen.getByTestId("workflow-row-script")).getByText("下一步：从空白开始")).toBeInTheDocument();
  });

  it("从空白开始直接建出空的正式脚本并刷新项目", async () => {
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "episode_1.json" });
    const refresh = vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    await renderExpanded(
      scenario({
        next: nextAction("start_blank_script"),
        content: { episode_source: "absent", formal_script: "absent", script_item_count: null },
        status: {
          artifacts: { script_plan: { state: "missing" } },
          operations: { prepare_script_plan: { state: "refused", reason: "episode_source_missing" } },
        },
      }),
    );
    const next = screen.getByTestId("workflow-next-step");
    expect(within(next).queryByRole("button", { name: "交给 Agent" })).not.toBeInTheDocument();
    fireEvent.click(within(next).getByRole("button", { name: "从空白开始" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("proj", 1));
    await waitFor(() => expect(refresh).toHaveBeenCalledWith("proj"));
  });
});

describe("WorkflowPanel 提醒", () => {
  it("过期、缺描述与未登记引用挂在资产图行，不进下一步", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("generate_storyboards", { requested_ids: ["E1S01"] }),
        content: {
          referenced_asset_sheets_stale: ["林默"],
          referenced_assets_without_description: ["祠堂"],
          referenced_assets_without_sheet: ["祠堂"],
          unregistered_references: ["路人"],
        },
      }),
    );
    const assets = screen.getByTestId("workflow-row-assets");
    expect(within(assets).getByText("1 张资产图已过期：林默")).toBeInTheDocument();
    expect(within(assets).getByText("1 个资产缺描述，不能生成资产图：祠堂")).toBeInTheDocument();
    expect(within(assets).getByText("1 个引用没有登记：路人")).toBeInTheDocument();
    // 缺描述的资产不算待生成，但也不说成都有资产图
    expect(within(assets).getByText("本集引用的资产有 1 个还没有资产图")).toBeInTheDocument();
    expect(within(assets).queryByText(/待生成/)).not.toBeInTheDocument();
    expect(within(assets).queryByTestId("workflow-next-step")).not.toBeInTheDocument();
    const next = screen.getByTestId("workflow-next-step");
    expect(within(next).queryByText(/林默|祠堂|路人/)).not.toBeInTheDocument();
    // 分镜图的生成入口会拒绝引用无图资产的分镜：下一步只附一条数量提示
    expect(within(next).getByText("1 个引用的资产还没有资产图，引用它们的分镜不能生成。")).toBeInTheDocument();
  });

  it("资产图行的过期资产图只作提醒，没有重新生成入口", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("generate_videos"),
        steps: [makeStep({ id: "asset_sheets", state: "completed", artifacts: { stale_ids: ["林默"] } })],
      }),
      { onRegenerate: vi.fn() },
    );
    expect(screen.queryByRole("button", { name: /重新生成/ })).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 草稿", () => {
  const repairDraft = { kind: "drama_script_plan", path: "drafts/episode_1.json", needs_repair: true };

  it("待修复草稿：交给 Agent 时带上违约清单，或者手动修改 / 丢弃草稿", async () => {
    vi.spyOn(API, "getEpisodeDraft").mockResolvedValue({
      doc_type: "drama_script_plan",
      revision: "rev-1",
      editable_by: "user",
      content: {},
      violations: [{ item_id: "E1S01", message: "时长不在档位内" }],
      soft_violations: [],
      formal_exists: false,
      episode: 1,
      item_ids: null,
    } as unknown as Awaited<ReturnType<typeof API.getEpisodeDraft>>);
    await renderExpanded(
      scenario({
        next: nextAction("resolve_draft", { args: { episode_id: 1, draft_kind: "drama_script_plan", needs_repair: true } }),
        content: { drafts: [repairDraft], formal_script: "absent", script_item_count: null },
      }),
    );
    const plan = screen.getByTestId("workflow-row-plan");
    expect(within(plan).getByText("待修复草稿")).toBeInTheDocument();
    const next = within(plan).getByTestId("workflow-next-step");
    expect(within(next).getByRole("button", { name: "手动修改" })).toBeInTheDocument();

    fireEvent.click(within(next).getByRole("button", { name: "交给 Agent" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("时长不在档位内"));
  });

  it("待修复草稿可以直接 AI 修复：按读到的版本提交，附加指令只随本次修复", async () => {
    useTasksStore.getState().setTasks([]);
    vi.spyOn(API, "getEpisodeDraft").mockResolvedValue({
      doc_type: "drama_script_plan",
      revision: "rev-3",
      editable_by: "user",
      content: {},
      violations: [{ item_id: "E1S01", message: "时长不在档位内" }],
      soft_violations: [],
      formal_exists: false,
      episode: 1,
      item_ids: null,
    } as unknown as Awaited<ReturnType<typeof API.getEpisodeDraft>>);
    const repair = vi
      .spyOn(API, "repairEpisodeDraft")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.repairEpisodeDraft>>);
    await renderExpanded(
      scenario({
        next: nextAction("resolve_draft", { args: { episode_id: 1, draft_kind: "drama_script_plan", needs_repair: true } }),
        content: { drafts: [repairDraft], formal_script: "absent", script_item_count: null },
      }),
    );
    const next = screen.getByTestId("workflow-next-step");
    fireEvent.change(within(next).getByRole("textbox"), { target: { value: "只调整时长" } });
    fireEvent.click(within(next).getByRole("button", { name: "AI 修复" }));

    await waitFor(() => expect(repair).toHaveBeenCalledWith("proj", 1, "drama_script_plan", "rev-3", "只调整时长"));
  });

  it("脚本规划的待修复草稿可以重新规划，打开的弹窗先说明会替换草稿", async () => {
    useScriptPlanStore.getState().close();
    await renderExpanded(
      scenario({
        next: nextAction("resolve_draft", { args: { episode_id: 1, draft_kind: "drama_script_plan", needs_repair: true } }),
        content: { drafts: [repairDraft], formal_script: "absent", script_item_count: null },
      }),
    );
    fireEvent.click(within(screen.getByTestId("workflow-next-step")).getByRole("button", { name: "重新规划" }));
    expect(useScriptPlanStore.getState().request).toEqual({ projectName: "proj", episode: 1, replaces: "draft" });
  });

  it("丢弃草稿先确认，写明会回到哪份内容，确认后按读到的版本丢弃", async () => {
    vi.spyOn(API, "getEpisodeDraft").mockResolvedValue({
      doc_type: "drama_script_plan",
      revision: "rev-7",
      editable_by: "user",
      content: {},
      violations: [],
      soft_violations: [],
      formal_exists: false,
      episode: 1,
      item_ids: null,
    } as unknown as Awaited<ReturnType<typeof API.getEpisodeDraft>>);
    const discard = vi
      .spyOn(API, "discardEpisodeDraft")
      .mockResolvedValue({ episode: 1, doc_type: "drama_script_plan", discarded: true });
    await renderExpanded(
      scenario({
        next: nextAction("resolve_draft", { args: { episode_id: 1, draft_kind: "drama_script_plan", needs_repair: true } }),
        content: { drafts: [repairDraft], formal_script: "absent", script_item_count: null },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "丢弃草稿" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/本集回到未规划状态/)).toBeInTheDocument();
    expect(discard).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "丢弃草稿" }));
    await waitFor(() => expect(discard).toHaveBeenCalledWith("proj", 1, "drama_script_plan", "rev-7"));
  });

  it("Agent 的可编辑草稿：交给 Agent 完成，或者丢弃这份修改", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("resolve_draft", { args: { episode_id: 1, draft_kind: "narration_script_plan", needs_repair: false } }),
        content: {
          drafts: [{ kind: "narration_script_plan", path: "drafts/episode_1.json", needs_repair: false }],
          formal_script: "absent",
          script_item_count: null,
        },
        status: { project: { content_mode: "narration", generation_mode: "storyboard", grid_storyboard: false } },
      }),
    );
    const next = screen.getByTestId("workflow-next-step");
    expect(within(next).getByRole("button", { name: "交给 Agent 完成" })).toBeInTheDocument();
    expect(within(next).getByRole("button", { name: "丢弃这份修改" })).toBeInTheDocument();
    expect(within(next).queryByRole("button", { name: "手动修改" })).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 编写提示词", () => {
  const plan = () =>
    scenario({ next: nextAction("author_prompts"), content: { pending_authoring_ids: ["E1S02"] } });

  it("AI 编写直接提交全部待编写，并带上附加指令", async () => {
    useTasksStore.getState().setTasks([]);
    const submit = vi
      .spyOn(API, "authorPrompts")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.authorPrompts>>);
    await renderExpanded(plan(), { onAuthorPrompts: vi.fn() });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "少用特写" } });
    fireEvent.click(within(screen.getByTestId("workflow-next-step")).getByRole("button", { name: "AI 编写" }));
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("proj", 1, {
        entry_ids: null,
        rewrite: false,
        instructions: "少用特写",
        overwrite_revision: null,
      }),
    );
  });

  it("交给 Agent 先按集保存附加指令，再预填范围与要求", async () => {
    const save = vi.spyOn(API, "savePromptAuthoringInstructions").mockResolvedValue({ success: true });
    await renderExpanded(plan(), { onAuthorPrompts: vi.fn() });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "少用特写" } });
    fireEvent.click(within(screen.getByTestId("workflow-next-step")).getByRole("button", { name: "交给 Agent" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("少用特写"));
    expect(save).toHaveBeenCalledWith("proj", 1, "少用特写");
  });
});

describe("WorkflowPanel AI 规划脚本", () => {
  const plan = () =>
    scenario({
      next: nextAction("prepare_script_plan"),
      content: { formal_script: "absent", script_item_count: null },
      status: { artifacts: { script_plan: { state: "missing" } } },
    });

  it("AI 规划脚本直接提交，附加指令预填本集保存的内容", async () => {
    useTasksStore.getState().setTasks([]);
    useProjectsStore.setState({
      currentProjectData: {
        episodes: [{ episode: 1, title: "第一集", script_file: "", script_plan_instructions: "多保留对白" }],
      } as unknown as ProjectData,
    });
    const submit = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);
    await renderExpanded(plan());
    expect(screen.getByRole("textbox")).toHaveValue("多保留对白");
    fireEvent.click(within(screen.getByTestId("workflow-next-step")).getByRole("button", { name: "AI 规划脚本" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith("proj", 1, { instructions: "多保留对白" }));
  });

  it("交给 Agent 先按集保存附加指令，再预填", async () => {
    const save = vi.spyOn(API, "saveScriptPlanInstructions").mockResolvedValue({ success: true });
    await renderExpanded(plan());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "节奏紧凑" } });
    fireEvent.click(within(screen.getByTestId("workflow-next-step")).getByRole("button", { name: "交给 Agent" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("节奏紧凑"));
    expect(save).toHaveBeenCalledWith("proj", 1, "节奏紧凑");
  });
});

describe("WorkflowPanel 集层资产图入口", () => {
  it("下一步是生成本集资产图时，直接调用打开集范围的批量确认", async () => {
    const preview = vi
      .spyOn(API, "previewAssetSheetBatch")
      .mockResolvedValue({ targets: [], skipped: [], estimated_cost: null });
    await renderPanel(
      scenario({
        next: nextAction("generate_asset_sheets", { args: { episode_id: 1 }, requested_ids: ["庭院"] }),
        content: { referenced_assets_without_sheet: ["庭院"] },
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "生成 1 张资产图" }));
    await waitFor(() => expect(preview).toHaveBeenCalledWith("proj", { episode_id: 1 }));
  });
});

describe("WorkflowPanel 分镜图与视频批量入口", () => {
  it.each([
    ["generate_storyboards", "批量生成分镜图", "storyboards"],
    ["generate_videos", "批量生成视频", "videos"],
  ] as const)("下一步是 %s 时，直接调用打开本集的批量确认", async (action, label, kind) => {
    const preview = vi
      .spyOn(API, "previewStoryboardBatch")
      .mockResolvedValue({ targets: [], skipped: [], estimated_cost: null });
    await renderPanel(
      scenario({
        next: nextAction(action, { requested_ids: ["E1S01"] }),
        status: { project: { content_mode: "narration", generation_mode: "storyboard", grid_storyboard: false } },
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: label }));
    await waitFor(() => expect(preview).toHaveBeenCalledWith("proj", 1, kind, expect.anything()));
  });

  it("参考生视频项目的视频下一步不给分镜视频批量入口", async () => {
    await renderPanel(
      scenario({
        next: nextAction("generate_videos"),
        status: { project: { content_mode: "narration", generation_mode: "reference_video", grid_storyboard: false } },
      }),
    );
    await screen.findByRole("button", { name: "交给 Agent" });
    expect(screen.queryByRole("button", { name: "批量生成视频" })).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 过期产物、任务与准入", () => {
  const videoScenario = (step: Partial<WorkflowPlanStep>) =>
    scenario({ next: nextAction("generate_videos"), steps: [makeStep({ id: "video", ...step })] });

  it("视频行的过期产物给出查看与显式重生入口", async () => {
    const onRegenerate = vi.fn();
    const onViewUnit = vi.fn();
    await renderExpanded(
      videoScenario({ artifacts: { current_ids: ["E1U1"], stale_ids: ["E1U2"], missing_ids: [] } }),
      { onRegenerate, onViewUnit },
    );
    fireEvent.click(screen.getByRole("button", { name: "在画布上查看 U2" }));
    expect(onViewUnit).toHaveBeenCalledWith("E1U2");
    fireEvent.click(screen.getByRole("button", { name: "重新生成 U2" }));
    expect(onRegenerate).toHaveBeenCalledWith("video", ["E1U2"]);
  });

  it("刷新失败不清空已经取到的计划", async () => {
    const spy = mockPlan(videoScenario({ artifacts: { current_ids: [], stale_ids: ["E1U2"], missing_ids: [] } }));
    render(<WorkflowPanel projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /制作进度/ }));
    await screen.findByText(/仍然保留，可以在画布上查看/);

    spy.mockRejectedValueOnce(new Error("offline"));
    await useWorkflowStore.getState().refreshPlan("proj", 1);

    await screen.findByText(/状态刷新失败/);
    expect(screen.getByText(/仍然保留，可以在画布上查看/)).toBeInTheDocument();
  });

  it("进行中的任务落在所属行，已提交给供应商时说明重试可能再次计费", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("wait_for_task", { args: {} }),
        steps: [
          makeStep({
            id: "video",
            state: "active",
            tasks: [
              {
                unit_id: "E1U1",
                task_id: "t1",
                task_type: "video",
                status: "running",
                provider_checkpoint: { submitted: true, provider_id: "vidu", provider_job_id: "job-9" },
              },
            ],
          }),
        ],
      }),
    );
    const row = screen.getByTestId("workflow-row-videos");
    expect(within(row).getByText(/视频 · 生成中/)).toBeInTheDocument();
    expect(within(row).getByText(/已提交给 vidu/)).toBeInTheDocument();
    expect(within(row).getByText("下一步：等待生成完成")).toBeInTheDocument();
  });

  it("分集规划在跑时下一步落在原文行，给出查看规划进度的入口", async () => {
    await renderExpanded(
      scenario({
        next: nextAction("wait_for_task", { args: {} }),
        steps: [
          makeStep({
            id: "episode_plan",
            state: "active",
            tasks: [
              {
                unit_id: "episode-planning-next",
                task_id: "t1",
                task_type: "text_episode_plan",
                status: "queued",
              },
            ],
          }),
        ],
      }),
    );
    const row = screen.getByTestId("workflow-row-source");
    expect(within(row).getByText("下一步：分集规划进行中")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "查看规划进度" })).toBeInTheDocument();
  });

  it("整批准入交回的下一步可以交给 Agent", async () => {
    await renderPanel(scenario({ next: nextAction("retry") }));
    fireEvent.click(await screen.findByRole("button", { name: "交给 Agent" }));
    expect(useAssistantStore.getState().input).toContain("视频生成前需要解决的问题");
  });

  it("缺模型配置时下一步跳到设置，不交给 Agent", async () => {
    await renderPanel(scenario({ next: nextAction("configure_provider") }));
    expect(await screen.findByRole("button", { name: "去设置" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "交给 Agent" })).not.toBeInTheDocument();
  });

  it("需确认档位时确认动作只带回档位并重新求解，不直接入队", async () => {
    const spy = mockPlan(
      videoScenario({
        admission: {
          decision: "confirmation_required",
          operation: "generate_videos",
          selection: "missing_only",
          units: [],
          confirmation: {
            tiers: [
              { request_duration_seconds: 8, unit_count: 2, unit_ids: ["E1U1", "E1U2"], cost_amount: 1.6, cost_currency: "USD" },
            ],
          },
        },
      }),
    );
    render(<WorkflowPanel projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /制作进度/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认这些档位" }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        "proj",
        expect.objectContaining({ confirmed_request_durations: { E1U1: 8, E1U2: 8 } }),
        expect.anything(),
      ),
    );
  });

  it("项目整体不可用时走阻断摘要，给出字段位置，技术细节收进折叠区", async () => {
    await renderExpanded(
      makePlan({
        status: makeStatus({
          content: null,
          blockers: [{ code: "script_unreadable", path: "scripts/episode_1.json", reason: "JSONDecodeError line 3" }],
        }),
        blockers: [{ code: "script_unreadable", path: "scripts/episode_1.json", reason: "JSONDecodeError line 3" }],
      }),
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("scripts/episode_1.json")).toBeInTheDocument();
    expect(within(alert).getByText("JSONDecodeError line 3")).toBeInTheDocument();
  });
});

describe("WorkflowPanel 刷新纪律", () => {
  beforeEach(() => {
    useTasksStore.getState().setTasks([]);
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    useTasksStore.getState().setTasks([]);
  });

  async function advanceDebounce(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  /** 推过防抖窗口，把待发布的指纹变化结算掉。 */
  async function settleDebounce() {
    await advanceDebounce(400);
  }

  it("别的项目的任务状态跳变不惊动本项目的计划", async () => {
    const spy = mockPlan(makePlan());
    render(<WorkflowPanel projectName="proj" episode={1} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    act(() => {
      useTasksStore.getState().setTasks([makeTask({ task_id: "other-1", project_name: "another", status: "queued" })]);
    });
    act(() => {
      useTasksStore.getState().setTasks([makeTask({ task_id: "other-1", project_name: "another", status: "succeeded" })]);
    });
    await settleDebounce();

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("本项目任务连续跳状态时合并为一次求解", async () => {
    const spy = mockPlan(makePlan());
    render(<WorkflowPanel projectName="proj" episode={1} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    for (const status of ["queued", "running", "succeeded"] as const) {
      act(() => {
        useTasksStore.getState().setTasks([makeTask({ task_id: "t1", project_name: "proj", status })]);
      });
    }
    expect(spy).toHaveBeenCalledTimes(1);

    // shouldAdvanceTime 下假定时钟随真实时间插值前进，故两侧断言各留安全余量：
    // 100ms 远未触达 250ms 防抖窗口，settleDebounce 则已远超该窗口。
    await advanceDebounce(100);
    expect(spy).toHaveBeenCalledTimes(1);
    await settleDebounce();

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
