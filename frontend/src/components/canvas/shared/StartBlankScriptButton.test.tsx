import { useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { LeaveGuardProvider, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { createDeferred } from "@/test/deferred";
import { makeTask, makeWorkspaceResponse } from "@/test/factories";
import { seedTasks } from "@/test/task-state";

import { StartBlankScriptButton } from "./StartBlankScriptButton";

function stubWorkspaceRefresh() {
  return vi.spyOn(API, "getWorkspace").mockResolvedValue(
    makeWorkspaceResponse({
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [{ episode: 2, title: "第二集", script_file: "scripts/episode_2.json", script_status: "generated" }],
      characters: {},
      scenes: {},
      props: {},
    }),
  );
}

function DirtyEditorHarness({
  discardsPlan,
  save = async () => true,
  onDiscard,
}: {
  discardsPlan: boolean;
  save?: () => Promise<boolean>;
  onDiscard?: () => void;
}) {
  const [dirty, setDirty] = useState(true);
  useLeaveGuard({
    dirty,
    save: async () => {
      const ok = await save();
      if (ok) setDirty(false);
      return ok;
    },
    discard: () => {
      setDirty(false);
      onDiscard?.();
    },
  });
  return (
    <>
      <span data-testid="dirty-state">{dirty ? "dirty" : "clean"}</span>
      <StartBlankScriptButton projectName="demo" episode={2} discardsPlan={discardsPlan} />
    </>
  );
}

describe("StartBlankScriptButton", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    seedTasks([]);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  it("warns when the blank script is created but the project refresh fails", async () => {
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    render(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan={false} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "从空白开始" }));

    expect(start).toHaveBeenCalledWith("demo", 2);
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      }),
    );
  });

  it("opens discard confirmation without firing leave guard, and intercepts confirmation when edits are unsaved", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });
    const onDiscard = vi.fn();

    render(
      <LeaveGuardProvider>
        <DirtyEditorHarness discardsPlan onDiscard={onDiscard} />
      </LeaveGuardProvider>,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const discardDialog = await screen.findByRole("alertdialog", { name: "弃置未确认的脚本规划？" });
    expect(screen.queryByRole("alertdialog", { name: "有未保存的修改" })).not.toBeInTheDocument();

    await user.click(within(discardDialog).getByRole("button", { name: "取消" }));
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("dirty");
    expect(onDiscard).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const reopenedDialog = await screen.findByRole("alertdialog", { name: "弃置未确认的脚本规划？" });
    await user.click(within(reopenedDialog).getByRole("button", { name: "从空白开始" }));

    const leaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    await user.click(within(leaveDialog).getByRole("button", { name: "继续编辑" }));
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("dirty");
  });

  it("preserves unsaved edits if discarding fails after choosing discard, and clears them on success", async () => {
    const user = userEvent.setup();
    const start = vi
      .spyOn(API, "startBlankScript")
      .mockRejectedValueOnce(new Error("server busy"))
      .mockResolvedValueOnce({ success: true, script_file: "scripts/episode_2.json" });
    stubWorkspaceRefresh();
    const onDiscard = vi.fn();

    render(
      <LeaveGuardProvider>
        <DirtyEditorHarness discardsPlan onDiscard={onDiscard} />
      </LeaveGuardProvider>,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const discardDialog = await screen.findByRole("alertdialog", { name: "弃置未确认的脚本规划？" });
    await user.click(within(discardDialog).getByRole("button", { name: "从空白开始" }));
    const leaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    await user.click(within(leaveDialog).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "从空白开始失败：server busy",
        tone: "error",
      }),
    );
    expect(onDiscard).not.toHaveBeenCalled();
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("dirty");

    await user.click(within(discardDialog).getByRole("button", { name: "从空白开始" }));
    const retryLeaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    await user.click(within(retryLeaveDialog).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onDiscard).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("clean");
  });

  it("saves unsaved edits before starting blank script when choosing save and leave", async () => {
    const user = userEvent.setup();
    const save = vi.fn(async () => true);
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });
    stubWorkspaceRefresh();

    render(
      <LeaveGuardProvider>
        <DirtyEditorHarness discardsPlan save={save} />
      </LeaveGuardProvider>,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const discardDialog = await screen.findByRole("alertdialog", { name: "弃置未确认的脚本规划？" });
    await user.click(within(discardDialog).getByRole("button", { name: "从空白开始" }));
    const leaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    await user.click(within(leaveDialog).getByRole("button", { name: "保存并离开" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(start).toHaveBeenCalledWith("demo", 2));
  });

  it("intercepts direct start via leave guard when discardsPlan is false", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });
    stubWorkspaceRefresh();

    render(
      <LeaveGuardProvider>
        <DirtyEditorHarness discardsPlan={false} />
      </LeaveGuardProvider>,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const leaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    expect(start).not.toHaveBeenCalled();

    await user.click(within(leaveDialog).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("demo", 2));
  });

  it("disables entry while this episode has an active script plan task, but ignores other episodes", () => {
    seedTasks([
      makeTask({
        project_name: "demo",
        task_type: "text_drama_script_plan",
        resource_id: "episode-1",
        status: "running",
      }),
    ]);
    const { rerender } = render(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan />);
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeEnabled();

    act(() => {
      seedTasks([
        makeTask({
          project_name: "demo",
          task_type: "text_drama_script_plan",
          resource_id: "episode-2",
          status: "running",
        }),
      ]);
    });
    rerender(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan />);
    const button = screen.getByRole("button", { name: "从空白开始" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("本集正在规划脚本，请等待完成");
  });

  it("disables entry while project episode planning or episode script generation is active", () => {
    seedTasks([
      makeTask({
        project_name: "demo",
        task_type: "text_episode_plan",
        resource_id: "episode-planning",
        status: "queued",
      }),
    ]);
    const { rerender } = render(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan />);
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "从空白开始" })).toHaveAccessibleDescription(
      "分集规划正在进行，请等待完成或停止后再提交。",
    );

    act(() => {
      seedTasks([
        makeTask({
          project_name: "demo",
          task_type: "text_episode_script",
          resource_id: "episode-2",
          status: "running",
        }),
      ]);
    });
    rerender(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan={false} />);
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "从空白开始" })).toHaveAccessibleDescription(
      "本集正在生成脚本或编写提示词，请等待完成",
    );
  });

  it("disables destructive confirm button live when planning starts while dialog is open, and rechecks before submit", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });

    render(
      <LeaveGuardProvider>
        <DirtyEditorHarness
          discardsPlan
          save={async () => {
            seedTasks([
              makeTask({
                project_name: "demo",
                task_type: "text_reference_script_plan",
                resource_id: "episode-2",
                status: "running",
              }),
            ]);
            return true;
          }}
        />
      </LeaveGuardProvider>,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    const discardDialog = await screen.findByRole("alertdialog", { name: "弃置未确认的脚本规划？" });

    act(() => {
      seedTasks([
        makeTask({
          project_name: "demo",
          task_type: "text_episode_plan",
          resource_id: "episode-planning-next",
          status: "running",
        }),
      ]);
    });
    expect(within(discardDialog).getByRole("button", { name: "从空白开始" })).toBeDisabled();
    expect(within(discardDialog).getByRole("alert")).toHaveTextContent("分集规划正在进行，请等待完成或停止后再提交。");

    act(() => {
      seedTasks([]);
    });
    const confirmButton = within(discardDialog).getByRole("button", { name: "从空白开始" });
    expect(confirmButton).toBeEnabled();

    await user.click(confirmButton);
    const leaveDialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    await user.click(within(leaveDialog).getByRole("button", { name: "保存并离开" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "本集正在规划脚本，请等待完成",
        tone: "error",
      }),
    );
    expect(start).not.toHaveBeenCalled();
  });

  it("reports submitting state across start and refresh, and clears it on episode switch or unmount", async () => {
    const user = userEvent.setup();
    const startDeferred = createDeferred<{ success: boolean; script_file: string }>();
    const refreshDeferred = createDeferred<Awaited<ReturnType<typeof API.getWorkspace>>>();
    vi.spyOn(API, "startBlankScript")
      .mockImplementationOnce(() => startDeferred.promise)
      .mockImplementationOnce(() => new Promise(() => {}));
    vi.spyOn(API, "getWorkspace").mockImplementationOnce(() => refreshDeferred.promise);
    const states: boolean[] = [];

    const { rerender, unmount } = render(
      <StartBlankScriptButton
        projectName="demo"
        episode={2}
        discardsPlan={false}
        onSubmittingChange={(submitting) => states.push(submitting)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    expect(states).toEqual([true]);

    await act(async () => {
      startDeferred.resolve({ success: true, script_file: "scripts/episode_2.json" });
      await Promise.resolve();
    });
    expect(states).toEqual([true]);

    await act(async () => {
      refreshDeferred.resolve(
        makeWorkspaceResponse({
          title: "Demo",
          content_mode: "narration",
          style: "Anime",
          episodes: [{ episode: 2, title: "第二集", script_file: "scripts/episode_2.json", script_status: "generated" }],
          characters: {},
          scenes: {},
          props: {},
        }),
      );
      await Promise.resolve();
    });
    await waitFor(() => expect(states).toEqual([true, false]));

    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    expect(states).toEqual([true, false, true]);

    rerender(
      <StartBlankScriptButton
        projectName="demo"
        episode={3}
        discardsPlan={false}
        onSubmittingChange={(submitting) => states.push(submitting)}
      />,
    );
    expect(states).toEqual([true, false, true, false]);

    vi.spyOn(API, "startBlankScript").mockImplementationOnce(() => new Promise(() => {}));
    await user.click(screen.getByRole("button", { name: "从空白开始" }));
    expect(states).toEqual([true, false, true, false, true]);

    unmount();
    expect(states).toEqual([true, false, true, false, true, false]);
  });
});
