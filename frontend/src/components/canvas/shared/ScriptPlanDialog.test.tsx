import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import { useScriptPlanStore } from "@/stores/script-plan-store";
import { useTasksStore } from "@/stores/tasks-store";
import { ScriptPlanHost } from "./ScriptPlanDialog";

describe("ScriptPlanHost", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useTasksStore.getState().setTasks([]);
    useAssistantStore.getState().setInput("");
    useScriptPlanStore.getState().close();
  });

  it("warns that replacing an unconfirmed plan loses its edits, and says so in the Agent hand-off", async () => {
    vi.spyOn(API, "saveScriptPlanInstructions").mockResolvedValue({ success: true });
    render(<ScriptPlanHost projectName="p" episode={1} savedInstructions="多保留对白" />);
    act(() => useScriptPlanStore.getState().open({ projectName: "p", episode: 1, replaces: "pending_plan" }));

    const dialog = await screen.findByRole("dialog", { name: "重新规划脚本" });
    expect(within(dialog).getByText(/在内容确认页做的修改都会丢失/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "交给 Agent" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const input = useAssistantStore.getState().input;
    expect(input).toContain("我已知道其中的修改会丢失");
    expect(input).toContain("附加指令：多保留对白");
  });

  it("re-plans with AI and closes once the request is accepted", async () => {
    const plan = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);
    render(<ScriptPlanHost projectName="p" episode={1} />);
    act(() => useScriptPlanStore.getState().open({ projectName: "p", episode: 1, replaces: "confirmed_plan" }));

    const dialog = await screen.findByRole("dialog", { name: "重新规划脚本" });
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "压缩到三场" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "AI 重新规划" }));

    await waitFor(() => expect(plan).toHaveBeenCalledWith("p", 1, { instructions: "压缩到三场" }));
    await waitFor(() => expect(useScriptPlanStore.getState().request).toBeNull());
  });

  it("keeps the dialog open when the request fails", async () => {
    vi.spyOn(API, "planScript").mockRejectedValue(new Error("服务不可用"));
    render(<ScriptPlanHost projectName="p" episode={1} />);
    act(() => useScriptPlanStore.getState().open({ projectName: "p", episode: 1, replaces: "confirmed_plan" }));

    fireEvent.click(await screen.findByRole("button", { name: "AI 重新规划" }));

    await waitFor(() => expect(API.planScript).toHaveBeenCalled());
    expect(screen.getByRole("dialog", { name: "重新规划脚本" })).toBeInTheDocument();
  });
});
