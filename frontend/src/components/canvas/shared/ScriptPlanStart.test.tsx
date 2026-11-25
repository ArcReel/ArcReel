import { seedTasks } from "@/test/task-state";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { makePlan, makeStatus, makeTask } from "@/test/factories";
import type { TaskProgress } from "@/types";
import { ScriptPlanStart } from "./ScriptPlanStart";

function renderStart(savedInstructions = "") {
  render(<ScriptPlanStart projectName="p" episode={1} savedInstructions={savedInstructions} />);
}

describe("ScriptPlanStart", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    seedTasks([]);
    useAssistantStore.getState().setInput("");
    useWorkflowStore.setState({ plan: null, planKey: null });
  });

  it("starts AI planning with the saved instructions", async () => {
    const plan = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);
    renderStart("多保留对白");

    expect(screen.getByRole("textbox", { name: "附加指令（可选）" })).toHaveValue("多保留对白");
    fireEvent.click(screen.getByRole("button", { name: "AI 规划" }));

    await waitFor(() => expect(plan).toHaveBeenCalledWith("p", 1, { instructions: "多保留对白" }));
  });

  it("hands off to the Agent after saving the instructions for this episode", async () => {
    const save = vi.spyOn(API, "saveScriptPlanInstructions").mockResolvedValue({ success: true });
    renderStart();

    fireEvent.change(screen.getByRole("textbox", { name: "附加指令（可选）" }), { target: { value: " 节奏紧凑 " } });
    fireEvent.click(screen.getByRole("button", { name: "交给 Agent" }));

    await waitFor(() => expect(useAssistantStore.getState().input).toContain("附加指令：节奏紧凑"));
    expect(save).toHaveBeenCalledWith("p", 1, "节奏紧凑");
    expect(useAssistantStore.getState().input).not.toContain("会丢失");
  });

  it("switching to another episode shows and submits that episode's saved instructions", async () => {
    const plan = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);
    const { rerender } = render(<ScriptPlanStart projectName="p" episode={1} savedInstructions="第一集的指令" />);
    fireEvent.change(screen.getByRole("textbox", { name: "附加指令（可选）" }), { target: { value: "第一集改到一半" } });

    rerender(<ScriptPlanStart projectName="p" episode={2} savedInstructions="第二集的指令" />);
    expect(screen.getByRole("textbox", { name: "附加指令（可选）" })).toHaveValue("第二集的指令");
    fireEvent.click(screen.getByRole("button", { name: "AI 规划" }));

    await waitFor(() => expect(plan).toHaveBeenCalledWith("p", 2, { instructions: "第二集的指令" }));
  });

  it("follows instructions saved elsewhere until the field is edited, then keeps the edit", () => {
    const { rerender } = render(<ScriptPlanStart projectName="p" episode={1} savedInstructions="旧指令" />);
    const field = screen.getByRole("textbox", { name: "附加指令（可选）" });

    rerender(<ScriptPlanStart projectName="p" episode={1} savedInstructions="别处保存的指令" />);
    expect(field).toHaveValue("别处保存的指令");

    fireEvent.change(field, { target: { value: "自己改的指令" } });
    rerender(<ScriptPlanStart projectName="p" episode={1} savedInstructions="又一次保存的指令" />);
    expect(field).toHaveValue("自己改的指令");
  });

  it("replaces the form with a status line while this episode is being planned", () => {
    seedTasks([
        makeTask({ project_name: "p", task_type: "text_script_plan", resource_id: "episode-1", status: "running" }),
      ]);
    renderStart();

    expect(screen.getByText("AI 正在规划脚本。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "AI 规划" })).not.toBeInTheDocument();
  });

  it("disables both planning actions with the refusal reason when planning is not admitted", () => {
    const status = makeStatus({
      operations: { prepare_script_plan: { state: "refused", reason: "episode_source_missing" } },
    });
    useWorkflowStore.setState({ plan: makePlan({ status }), planKey: "p::1" });
    renderStart();

    for (const name of ["交给 Agent", "AI 规划"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription("需要先补充本集原文");
    }
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeEnabled();
  });

  it("renders live text task phases, elapsed time, and the 15s long-wait hint on controllable clock", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-11-01T12:00:10Z"));
      const task = makeTask({
        task_id: "plan-run-1",
        project_name: "p",
        task_type: "text_drama_script_plan",
        media_type: "text",
        resource_id: "episode-1",
        status: "running",
        started_at: "2026-11-01T12:00:00Z",
      });
      seedTasks([task]);
      renderStart();

      const status = screen.getByRole("status");
      expect(screen.getByText("AI 正在规划脚本。")).toBeVisible();
      expect(screen.getByLabelText(/已运行.*10/)).toBeInTheDocument();
      const update = (event: TaskProgress) => act(() => useTasksStore.getState().receiveProgress(event));
      update({ task_id: task.task_id, seq: 1, phase: "waiting", model: "Claude" });
      expect(status).toHaveTextContent("正在等待 Claude 响应…");
      update({ task_id: task.task_id, seq: 2, phase: "receiving", thinking: true, characters: 0 });
      expect(status).toHaveTextContent("模型思考中…");
      update({ task_id: task.task_id, seq: 3, phase: "receiving", thinking: false, characters: 320 });
      expect(status).toHaveTextContent(/^正在接收生成内容…$/);
      expect(screen.getByText(/已接收约 320 字/)).toBeVisible();
      update({ task_id: task.task_id, seq: 4, phase: "retrying", attempt: 2, max_attempts: 3, error: "RateLimitError" });
      expect(status).toHaveTextContent("正在重试（第 2/3 次尝试）…");

      act(() => vi.advanceTimersByTime(5_000));
      expect(screen.queryByText(/长文本通常需要数十秒至数分钟/)).not.toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1_000));
      expect(screen.getByText(/长文本通常需要数十秒至数分钟/)).toHaveAttribute("aria-live", "polite");
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows non-blocking guidance only when episode source exceeds 15,000 characters", async () => {
    const plan = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);
    const { rerender } = render(
      <ScriptPlanStart projectName="p" episode={1} savedInstructions="" sourceCharCount={15_000} />,
    );
    expect(screen.queryByText(/超过 15,000 字/)).not.toBeInTheDocument();

    rerender(<ScriptPlanStart projectName="p" episode={1} savedInstructions="" sourceCharCount={15_001} />);
    expect(screen.getByText(/本集原文约 15,001 字，超过 15,000 字/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去分集" })).toHaveAttribute("href", "/episodes?episode=1");

    const aiButton = screen.getByRole("button", { name: "AI 规划" });
    expect(aiButton).toBeEnabled();
    fireEvent.click(aiButton);
    await waitFor(() => expect(plan).toHaveBeenCalledWith("p", 1, { instructions: null }));
  });
});
