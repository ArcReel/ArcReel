import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

function HostedTaskHud() {
  const anchorRef = useRef<HTMLDivElement>(null);
  return <><div ref={anchorRef} /><TaskHud anchorRef={anchorRef} /></>;
}

function openHudWith(tasks: ReturnType<typeof makeTask>[]) {
  useAppStore.setState({ taskHudOpen: true });
  useTasksStore.setState({ tasks });
  render(<HostedTaskHud />);
}

describe("TaskHud artifact download retry", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("replaces the failed row with the resumed task the server returns", async () => {
    const failed = makeTask({
      task_id: "download-1",
      status: "failed",
      error_message: "download failed",
      error_code: "artifact_download_failed",
    });
    const running = { ...failed, status: "running" as const, error_message: null, error_code: undefined };
    vi.spyOn(API, "retryTaskDownload").mockResolvedValue({ task: running });
    openHudWith([failed]);

    fireEvent.click(await screen.findByRole("button", { name: "重试下载" }));

    await waitFor(() => {
      expect(useTasksStore.getState().tasks).toEqual([running]);
    });
    expect(screen.queryByRole("button", { name: "重试下载" })).not.toBeInTheDocument();
  });

  it("keeps each row's retry pending until its own request settles", async () => {
    const failed = (id: string) =>
      makeTask({
        task_id: id,
        status: "failed",
        error_message: "download failed",
        error_code: "artifact_download_failed",
      });
    const first = failed("download-a");
    const second = failed("download-b");
    let settleFirst: (value: { task: ReturnType<typeof makeTask> }) => void = () => {};
    vi.spyOn(API, "retryTaskDownload").mockImplementation((taskId: string) =>
      taskId === "download-a"
        ? new Promise((resolve) => {
          settleFirst = resolve;
        })
        : Promise.resolve({ task: { ...second, status: "running" as const } })
    );
    openHudWith([first, second]);

    const buttons = await screen.findAllByRole("button", { name: "重试下载" });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);

    // 后点的那条已返回、先点的那条仍在途时，先点的那条的按钮仍禁着：每一行的在途状态
    // 只由它自己的请求决定。判据落在加载态上——任务状态看不出这一位。
    await waitFor(() => {
      expect(useTasksStore.getState().tasks.map((task) => task.status)).toEqual(["failed", "running"]);
    });
    expect(buttons[0]).toBeDisabled();
    settleFirst({ task: { ...first, status: "running" as const } });
    await waitFor(() => {
      expect(useTasksStore.getState().tasks.map((task) => task.status)).toEqual(["running", "running"]);
    });
  });

  it("tells the user when the retry could not be started", async () => {
    const failed = makeTask({
      task_id: "download-3",
      status: "failed",
      error_message: "download failed",
      error_code: "artifact_download_failed",
    });
    vi.spyOn(API, "retryTaskDownload").mockRejectedValue(new Error("task is no longer eligible"));
    openHudWith([failed]);

    fireEvent.click(await screen.findByRole("button", { name: "重试下载" }));

    // 失败时这一行看不出任何变化，回执只能来自 toast。
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toBe("重试下载没能开始，请展开任务查看最新状态");
    });
    expect(useAppStore.getState().toast?.tone).toBe("error");
  });

  it("offers the action only on artifact download failures", () => {
    openHudWith([
      makeTask({
        task_id: "download-2",
        status: "failed",
        error_message: "provider rejected the prompt",
        error_code: "provider_error",
      }),
    ]);

    expect(screen.queryByRole("button", { name: "重试下载" })).not.toBeInTheDocument();
  });
});
