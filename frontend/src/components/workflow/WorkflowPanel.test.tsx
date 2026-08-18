import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { WorkflowPanel } from "./WorkflowPanel";
import { makePlan, makeStep, makeTask } from "@/test/factories";
import type { WorkflowPlan } from "@/types/workflow";

function mockPlan(plan: WorkflowPlan) {
  return vi.spyOn(API, "getWorkflowPlan").mockResolvedValue(plan);
}

/** 面板默认收起；绝大多数断言都要先展开。 */
async function renderExpanded(plan: WorkflowPlan, props: Partial<React.ComponentProps<typeof WorkflowPanel>> = {}) {
  mockPlan(plan);
  render(<WorkflowPanel projectName="proj" episode={1} {...props} />);
  const toggle = await screen.findByRole("button", { name: /制作状态/ });
  fireEvent.click(toggle);
  return toggle;
}

beforeEach(() => {
  useWorkflowStore.getState().resetTarget();
});

describe("WorkflowPanel 状态语言", () => {
  it("六种步骤状态各自有独立措辞，不塌成一个词", async () => {
    const states = ["completed", "ready", "active", "blocked", "pending", "skipped"] as const;
    await renderExpanded(
      makePlan({
        steps: states.map((state, index) => makeStep({ id: `step_${index}`, state })),
      }),
    );

    const labels = states.map((state) => {
      const row = screen.getByTestId(`workflow-step-step_${states.indexOf(state)}`);
      return within(row).getByText(
        /已完成|可以开始|进行中|受阻|尚未开始|本模式不涉及/,
      ).textContent;
    });
    expect(new Set(labels).size).toBe(states.length);
  });

  it("跳过的步骤留在列表里并说明本模式不涉及", async () => {
    await renderExpanded(
      makePlan({ steps: [makeStep({ id: "selling_points", state: "skipped" })] }),
    );
    const row = screen.getByTestId("workflow-step-selling_points");
    expect(within(row).getByText("这类项目不走这一步。")).toBeInTheDocument();
  });

  it("产物按 current / stale / missing 分列陈述，stale 计入可用而非缺口", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "storyboard",
            artifacts: {
              current_ids: ["E1S01", "E1S02"],
              stale_ids: ["E1S03"],
              missing_ids: ["E1S04"],
            },
          }),
        ],
      }),
    );
    const row = screen.getByTestId("workflow-step-storyboard");
    expect(within(row).getByText(/可用 3 件/)).toBeInTheDocument();
    expect(within(row).getByText(/其中 1 件比当前内容旧/)).toBeInTheDocument();
    expect(within(row).getByText(/还差 1 件/)).toBeInTheDocument();
  });

  it("产物状态词超出产物时效四种取值时照常陈述，不在查表上崩掉整个面板", async () => {
    // 资产盘点只盘了一部分源文时后端给的是 `partial`，它不属于产物时效那四种取值。
    await renderExpanded(
      makePlan({
        steps: [makeStep({ id: "asset_inventory", artifacts: { state: "partial" } })],
      }),
    );
    const row = screen.getByTestId("workflow-step-asset_inventory");
    expect(within(row).getByText("只覆盖了一部分内容")).toBeInTheDocument();
  });

  it("状态词是后端新加的、面板还没有译文时复述原词，而不是显示译文键", async () => {
    await renderExpanded(
      makePlan({
        steps: [makeStep({ id: "asset_inventory", artifacts: { state: "quarantined" } })],
      }),
    );
    const row = screen.getByTestId("workflow-step-asset_inventory");
    expect(within(row).getByText("状态：quarantined")).toBeInTheDocument();
  });

  it("集合读不出来时只说读不出来，不把任何 id 猜成缺失", async () => {
    await renderExpanded(
      makePlan({ steps: [makeStep({ id: "storyboard", artifacts: { state: "blocked" } })] }),
    );
    const row = screen.getByTestId("workflow-step-storyboard");
    expect(within(row).getByText(/这个目录读不出来/)).toBeInTheDocument();
    expect(within(row).queryByText(/还差/)).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 已过时产物", () => {
  const stalePlan = makePlan({
    steps: [
      makeStep({
        id: "video",
        artifacts: { current_ids: ["E1U1"], stale_ids: ["E1U2"], missing_ids: [] },
      }),
    ],
  });

  it("过时产物声明文件保留可用，并给出查看与显式重生入口", async () => {
    const onRegenerate = vi.fn();
    const onViewUnit = vi.fn();
    await renderExpanded(stalePlan, { onRegenerate, onViewUnit });

    expect(screen.getByText(/仍然保留，可以在画布上查看/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "在画布上查看 E1U2" }));
    expect(onViewUnit).toHaveBeenCalledWith("E1U2");

    fireEvent.click(screen.getByRole("button", { name: "重新生成 E1U2" }));
    expect(onRegenerate).toHaveBeenCalledWith("video", ["E1U2"]);
  });

  it("没有重生回调时不长出点了没反应的按钮", async () => {
    await renderExpanded(stalePlan);
    expect(screen.queryByRole("button", { name: /重新生成/ })).not.toBeInTheDocument();
  });

  it("刷新失败不清空已经取到的计划", async () => {
    const spy = mockPlan(stalePlan);
    render(<WorkflowPanel projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /制作状态/ }));
    await screen.findByText(/仍然保留，可以在画布上查看/);

    spy.mockRejectedValueOnce(new Error("offline"));
    await useWorkflowStore.getState().refreshPlan("proj", 1);

    await screen.findByText(/状态刷新失败/);
    expect(screen.getByText(/仍然保留，可以在画布上查看/)).toBeInTheDocument();
  });
});

describe("WorkflowPanel 任务与 provider checkpoint", () => {
  it("恢复中的任务落在尝试这一轴，不被算成 current 产物", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "video",
            state: "active",
            artifacts: { current_ids: [], stale_ids: [], missing_ids: ["E1U1"] },
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
    const row = screen.getByTestId("workflow-step-video");
    expect(within(row).getByText(/1 次尝试进行中/)).toBeInTheDocument();
    expect(within(row).getByText(/视频 · 生成中/)).toBeInTheDocument();
    // 产物这一轴照旧报缺失——一次尝试在跑不等于已经有成片
    expect(within(row).getByText(/可用 0 件 · 还差 1 件/)).toBeInTheDocument();
    // provider checkpoint 自己占一行，说明重试可能重复计费
    expect(within(row).getByText(/已提交给 vidu/)).toBeInTheDocument();
    expect(within(row).getByText("job-9")).toBeInTheDocument();
  });
});

describe("WorkflowPanel 结构化问题与阻断", () => {
  it("阻断走错误摘要，给出字段位置，技术细节收进折叠区", async () => {
    await renderExpanded(
      makePlan({
        status: undefined,
        blockers: [
          { code: "script_unreadable", path: "scripts/episode_1.json", reason: "JSONDecodeError line 3" },
        ],
      }),
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("scripts/episode_1.json")).toBeInTheDocument();
    expect(within(alert).getByText(/请智能体修复损坏的项目文件/)).toBeInTheDocument();
    expect(within(alert).getByText("JSONDecodeError line 3")).toBeInTheDocument();
    expect(within(alert).getByText("技术细节")).toBeInTheDocument();
  });

  it("需要重新规划的问题只陈述下一步，不提供自动拆分按钮", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "script_structure",
            state: "blocked",
            problems: [
              {
                code: "needs_replan",
                detail: "unit too long",
                action: "replan_unit",
                params: { unit_id: "E1U2" },
              },
            ],
          }),
        ],
      }),
    );
    const row = screen.getByTestId("workflow-step-script_structure");
    expect(within(row).getByText("E1U2")).toBeInTheDocument();
    expect(within(row).getByText(/改写这个单元/)).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /拆分|重新规划/ })).not.toBeInTheDocument();
  });
});

describe("WorkflowPanel 旁白交付", () => {
  const deliveryStep = makeStep({ id: "narration_delivery", state: "ready" });

  it("两种交付方式都在本次操作中可选，并说明不写回项目", async () => {
    await renderExpanded(makePlan({ steps: [deliveryStep] }));
    expect(screen.getByRole("radio", { name: "后期配音" })).toBeEnabled();
    expect(screen.getByRole("radio", { name: "使用已配置的语音合成" })).toBeEnabled();
    expect(screen.getByText(/只作用于本次生成，不写回项目设置/)).toBeInTheDocument();
  });

  it("选择后按该交付方式重新求解", async () => {
    const spy = mockPlan(makePlan({ steps: [deliveryStep] }));
    render(<WorkflowPanel projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /制作状态/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "后期配音" }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        "proj",
        expect.objectContaining({ narration_delivery: "post_production" }),
        expect.anything(),
      ),
    );
  });

  it("未配置 TTS 时引导后期配音，且不把整个工作流标红", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "narration_delivery",
            state: "blocked",
            problems: [
              {
                code: "tts_not_configured",
                detail: "tts provider unavailable",
                action: "configure_provider",
                params: { path: ["generation_settings", "audio_backend"] },
              },
            ],
          }),
        ],
      }),
    );
    expect(screen.getByRole("radio", { name: "使用已配置的语音合成" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "后期配音" })).toBeEnabled();
    expect(screen.getByText(/选后期配音即可继续/)).toBeInTheDocument();
    // 只是一条路径没配好，不是整集受阻——面板不弹错误摘要
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("TTS 未配置这条问题落在视频步骤上时同样引导后期配音", async () => {
    // 这条问题由视频批量准入求解得出（选了 TTS 才跑那一轮），后端把它挂在计划的问题
    // 清单与视频步骤上，而不是旁白交付步骤。只翻交付步骤的 problems 会漏掉它。
    const problem = {
      code: "tts_not_configured",
      detail: "tts provider unavailable",
      action: "configure_provider",
      params: { path: ["generation_settings", "audio_backend"] },
    };
    await renderExpanded(
      makePlan({
        problems: [problem],
        steps: [
          makeStep({ id: "narration_delivery", state: "ready" }),
          makeStep({ id: "video", state: "blocked", problems: [problem] }),
        ],
      }),
    );
    expect(screen.getByRole("radio", { name: "使用已配置的语音合成" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "后期配音" })).toBeEnabled();
    expect(screen.getByText(/选后期配音即可继续/)).toBeInTheDocument();
  });
});

describe("WorkflowPanel 批量准入", () => {
  it("准入受阻时一次列出全部问题并说明零任务", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "video",
            state: "blocked",
            admission: {
              decision: "blocked",
              operation: "generate_videos",
              selection: "missing_only",
              narration_delivery: "post_production",
              units: [
                {
                  unit_id: "E1U1",
                  admitted: false,
                  problems: [
                    { code: "reference_asset_missing", action: "generate_dependency", message: "引用的角色图缺失" },
                  ],
                },
                {
                  unit_id: "E1U2",
                  admitted: false,
                  problems: [{ code: "needs_replan", action: "replan_unit", message: "该单元需要重新规划" }],
                },
                {
                  unit_id: "E1U3",
                  admitted: true,
                  withheld: true,
                  problems: [
                    {
                      code: "generation_batch_admission_withheld",
                      action: "retry",
                      params: { blocked_unit_ids: ["E1U1", "E1U2"] },
                    },
                  ],
                },
              ],
              confirmation: null,
            },
          }),
        ],
      }),
    );
    const row = screen.getByTestId("workflow-step-video");
    expect(within(row).getByText(/一个任务也没有创建，没有产生费用/)).toBeInTheDocument();
    expect(within(row).getByText("引用的角色图缺失")).toBeInTheDocument();
    expect(within(row).getByText("该单元需要重新规划")).toBeInTheDocument();
    expect(within(row).getByText(/1 个单元本身没问题/)).toBeInTheDocument();
    for (const unitId of ["E1U1", "E1U2", "E1U3"]) {
      expect(within(row).getByText(unitId)).toBeInTheDocument();
    }
  });

  it("需确认档位时确认动作只带回档位并重新求解，不直接入队", async () => {
    const spy = mockPlan(
      makePlan({
        steps: [
          makeStep({
            id: "video",
            admission: {
              decision: "confirmation_required",
              operation: "generate_videos",
              selection: "missing_only",
              narration_delivery: "post_production",
              units: [],
              confirmation: {
                tiers: [
                  {
                    request_duration_seconds: 8,
                    unit_count: 2,
                    unit_ids: ["E1U1", "E1U2"],
                    cost_amount: 1.6,
                    cost_currency: "USD",
                  },
                ],
              },
            },
          }),
        ],
      }),
    );
    render(<WorkflowPanel projectName="proj" episode={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /制作状态/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认这些档位" }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        "proj",
        expect.objectContaining({ confirmed_request_durations: { E1U1: 8, E1U2: 8 } }),
        expect.anything(),
      ),
    );
  });
});

describe("WorkflowPanel 布局与展开", () => {
  it("默认收起，摘要行只复述后端给的下一步", async () => {
    mockPlan(makePlan());
    render(<WorkflowPanel projectName="proj" episode={1} />);
    const toggle = await screen.findByRole("button", { name: /制作状态/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByText("下一步：生成缺失的视频")).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-step-video")).not.toBeInTheDocument();
  });

  it("窄屏下摘要行换行堆叠而不横向溢出", async () => {
    const toggle = await renderExpanded(
      makePlan({ blockers: [{ code: "script_unreadable", path: "scripts/e1.json", reason: "bad" }] }),
    );
    const summary = toggle.parentElement!;
    expect(summary.className).toContain("flex-wrap");
    // 摘要文本在窄屏下截断而不是把整行撑宽
    expect(summary.querySelector(".truncate")).not.toBeNull();
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

  /** 推进指定毫秒数的定时器。 */
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
      useTasksStore
        .getState()
        .setTasks([makeTask({ task_id: "other-1", project_name: "another", status: "queued" })]);
    });
    act(() => {
      useTasksStore
        .getState()
        .setTasks([makeTask({ task_id: "other-1", project_name: "another", status: "succeeded" })]);
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
        useTasksStore
          .getState()
          .setTasks([makeTask({ task_id: "t1", project_name: "proj", status })]);
      });
    }
    expect(spy).toHaveBeenCalledTimes(1);

    await advanceDebounce(249);
    expect(spy).toHaveBeenCalledTimes(1);
    await advanceDebounce(1);

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});

describe("WorkflowPanel 产品语言", () => {
  it("步骤使用新产品语言名称", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({ id: "step1_content", state: "pending" }),
          makeStep({ id: "step1_review", state: "pending" }),
          makeStep({ id: "asset_sheets", state: "pending" }),
        ],
      }),
    );
    const step1Row = screen.getByTestId("workflow-step-step1_content");
    expect(within(step1Row).getByText("内容整理")).toBeInTheDocument();
    const reviewRow = screen.getByTestId("workflow-step-step1_review");
    expect(within(reviewRow).getByText("内容确认")).toBeInTheDocument();
    const sheetsRow = screen.getByTestId("workflow-step-asset_sheets");
    expect(within(sheetsRow).getByText("资产图")).toBeInTheDocument();
  });

  it("任务类型标签使用新产品语言", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "storyboard",
            state: "active",
            tasks: [{ unit_id: "G01", task_id: "t1", task_type: "grid", status: "running" }],
          }),
        ],
      }),
    );
    expect(screen.getByText(/多宫格分镜/)).toBeInTheDocument();
  });

  it("动作短语中使用智能体而非助手", async () => {
    await renderExpanded(
      makePlan({
        steps: [
          makeStep({
            id: "final_script",
            state: "blocked",
            problems: [{
              code: "repair_project_data",
              detail: "corrupted",
              action: "repair_project_data",
              params: {},
            }],
          }),
        ],
      }),
    );
    expect(screen.getByText(/请智能体修复/)).toBeInTheDocument();
    expect(screen.queryByText(/请助手修复/)).not.toBeInTheDocument();
  });
});
