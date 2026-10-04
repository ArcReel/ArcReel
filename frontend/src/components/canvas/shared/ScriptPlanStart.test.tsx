import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { makePlan, makeStatus, makeTask } from "@/test/factories";
import { ScriptPlanStart } from "./ScriptPlanStart";

function renderStart(savedInstructions = "") {
  render(<ScriptPlanStart projectName="p" episode={1} savedInstructions={savedInstructions} />);
}

describe("ScriptPlanStart", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useTasksStore.getState().setTasks([]);
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
    useTasksStore
      .getState()
      .setTasks([
        makeTask({ project_name: "p", task_type: "text_script_plan", resource_id: "episode-1", status: "running" }),
      ]);
    renderStart();

    expect(screen.getByRole("status")).toHaveTextContent("AI 正在规划脚本。");
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
});
