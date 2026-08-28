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
