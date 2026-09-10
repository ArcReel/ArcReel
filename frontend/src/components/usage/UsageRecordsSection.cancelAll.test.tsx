import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { makeTask } from "@/test/factories";
import type { UsageRecordPage } from "@/types";
import { makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

const EMPTY_PAGE: UsageRecordPage = { items: [], next_cursor: null, total: 0 };
const PROJECT = "星海列车";
const PROJECT_QUERY = `section=usage&u_project=${encodeURIComponent(PROJECT)}`;

function queuedTask(taskId: string, resourceId: string) {
  return makeTask({
    task_id: taskId,
    project_name: PROJECT,
    task_type: "storyboard",
    media_type: "image",
    resource_id: resourceId,
    status: "queued",
  });
}

describe("UsageRecordsSection cancel-all", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    vi.spyOn(API, "getUsageRecords").mockResolvedValue(EMPTY_PAGE);
    vi.spyOn(API, "listTasks").mockImplementation(async () => {
      const tasks = useTasksStore.getState().tasks;
      return { items: tasks, total: tasks.length, page: 1, page_size: 200 };
    });
    vi.spyOn(API, "getTaskStats").mockResolvedValue({
      stats: {
        queued: 0,
        running: 0,
        cancelling: 0,
        succeeded: 0,
        failed: 0,
        cancelled: 0,
        total: 0,
      },
    });
  });

  it("cancels the queued tasks of the filtered project after confirmation", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    let cancelled = false;
    const cancelAllSpy = vi.spyOn(API, "cancelAllQueued").mockImplementation(async () => {
      cancelled = true;
      return { cancelled_count: 2, skipped_running_count: 0 };
    });
    useTasksStore.setState({
      tasks: [queuedTask("t-q1", "E1S10"), queuedTask("t-q2", "E1S11")],
    });
    vi.mocked(API.listTasks).mockImplementation(async () => {
      const tasks = cancelled ? [] : useTasksStore.getState().tasks;
      return { items: tasks, total: tasks.length, page: 1, page_size: 200 };
    });

    renderUsageRecordsSection(PROJECT_QUERY);

    fireEvent.click(await screen.findByRole("button", { name: "全部取消" }));

    const dialog = await screen.findByRole("alertdialog", { name: "取消确认" });
    expect(dialog).toHaveTextContent("确定取消所有 2 个排队中的任务？");

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => expect(cancelAllSpy).toHaveBeenCalledWith(PROJECT));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "全部取消" })).not.toBeInTheDocument(),
    );
  });

  it("hides the action when the in-progress block holds no queued task", async () => {
    useTasksStore.setState({
      tasks: [makeTask({ ...queuedTask("t-run", "E1S10"), status: "running" })],
    });

    renderUsageRecordsSection(PROJECT_QUERY);

    expect(await screen.findByText("分镜 E1S10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全部取消" })).not.toBeInTheDocument();
  });

  it("hides the action while no project is selected", async () => {
    useTasksStore.setState({ tasks: [queuedTask("t-q1", "E1S10")] });

    renderUsageRecordsSection("section=usage");

    expect(await screen.findByText("分镜 E1S10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全部取消" })).not.toBeInTheDocument();
  });

  it("hides the action when queued tasks belong to another project", async () => {
    useTasksStore.setState({
      tasks: [makeTask({ ...queuedTask("t-q1", "E1S10"), project_name: "雨夜侦探" })],
    });

    renderUsageRecordsSection(PROJECT_QUERY);

    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "全部取消" })).not.toBeInTheDocument();
  });
});
