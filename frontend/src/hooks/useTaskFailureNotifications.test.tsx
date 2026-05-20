import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import type { ProjectData, TaskItem } from "@/types";

function Harness({ project }: { project: string }) {
  useTaskFailureNotifications(project);
  return null;
}

function task(overrides: Partial<TaskItem>): TaskItem {
  return {
    task_id: "t1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "E1S01",
    script_file: "ep1.json",
    payload: {},
    status: "running",
    result: null,
    error_message: "boom",
    cancelled_by: null,
    source: "webui",
    queued_at: "",
    started_at: null,
    finished_at: null,
    updated_at: "",
    ...overrides,
  };
}

const PROJECT = {
  episodes: [{ episode: 1, title: "E1", script_file: "scripts/ep1.json" }],
} as unknown as ProjectData;

describe("useTaskFailureNotifications", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useTasksStore.setState({ tasks: [], connected: false });
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
  });

  it("pushes one clickable notification when a task transitions to failed", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);

    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
    });
    const note = useAppStore.getState().workspaceNotifications[0];
    expect(note.tone).toBe("error");
    expect(note.target).toEqual({
      type: "segment",
      id: "E1S01",
      route: "/episodes/1",
      highlight_style: "flash",
    });
  });

  it("does not notify for tasks already failed on first observation", () => {
    useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("ignores tasks from other projects", () => {
    useTasksStore.setState({ tasks: [task({ project_name: "other", status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ project_name: "other", status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  // 回归：worker 不清理失败任务，后续 poll 会反复带回同一条 failed 记录，
  // 只应通知一次（仅在 non-failed → failed 转变时）。
  it("notifies only once for the same failed task across repeated updates", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    // 同一 failed 任务在下一轮 poll 再次出现，不应再通知
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  it("builds a reference_unit target for reference_video failures", async () => {
    useTasksStore.setState({
      tasks: [task({ task_id: "r1", task_type: "reference_video", resource_id: "E1U1", status: "running" })],
    });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({
        tasks: [task({ task_id: "r1", task_type: "reference_video", resource_id: "E1U1", status: "failed" })],
      });
    });
    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications[0]?.target).toEqual({
        type: "reference_unit",
        id: "E1U1",
        route: "/episodes/1",
      });
    });
  });
});
