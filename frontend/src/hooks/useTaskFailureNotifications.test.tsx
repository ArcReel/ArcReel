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
    resource_type: null,
    script_file: "ep1.json",
    payload: {},
    status: "running",
    result: null,
    error_message: "boom",
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
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
    // connected=true simulates first successful polls, allowing baseline establishment.
    useTasksStore.setState({ tasks: [], connected: true });
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

  it("tells the user the result went to the trash when the target was deleted during generation", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" }), task({ task_id: "t2", status: "running" })] });
    render(<Harness project="demo" />);

    act(() => {
      useTasksStore.setState({
        tasks: [
          task({ status: "succeeded", result: { target_deleted: true, trash_record_id: "r1" } }),
          task({ task_id: "t2", status: "succeeded", result: { file_path: "storyboards/a.png" } }),
        ],
      });
    });

    await waitFor(() => {
      expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
    });
    const note = useAppStore.getState().workspaceNotifications[0];
    expect(note.tone).toBe("warning");
    expect(note.text).toContain("项目回收站");
  });

  it("does not notify for tasks already failed on first observation", () => {
    useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  // With 3-second polling, tasks may fail between polls, first observed already failed.
  // New tasks first appearing after baselines must notify, or rapid background failures disappear.
  it("notifies for a brand-new task that appears already-failed after the baseline", async () => {
    // Baseline: first successful poll includes historical failed tasks, absorbed without notices.
    useTasksStore.setState({ tasks: [task({ task_id: "old", status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
    // Next poll: new task_id appears failed directly after rapid between-poll failure.
    act(() => {
      useTasksStore.setState({
        tasks: [
          task({ task_id: "old", status: "failed" }),
          task({ task_id: "fast", status: "failed", resource_id: "E1S02" }),
        ],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S02" });
  });

  it("ignores tasks from other projects", () => {
    useTasksStore.setState({ tasks: [task({ project_name: "other", status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ project_name: "other", status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  // Regression: workers retain failed tasks, so later polls repeatedly return identical failed rows;
  // notify once only, on non-failed → failed transitions.
  it("notifies only once for the same failed task across repeated updates", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    // Identical failed tasks reappearing next poll must not renotify.
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  // Regression: A→B→A simulates leaving/returning to projects. Actual useTasksSSE order is
  // cleanup setConnected(false) → new poll → setTasks(new project) + setConnected(true).
  // Previously transition-notified historical A failures must not renotify as fresh on return.
  it("does not re-notify historical failed tasks when leaving and returning to the project", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })], connected: true });
    const { rerender } = render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));

    // Switch projects: projectName changes first, then simulate useTasksSSE cleanup+setup:
    // setConnected(false) → setTasks(empty) + setConnected(true).
    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      useTasksStore.setState({ tasks: [], connected: true });
    });

    // Return to demo with identical useTasksSSE timing; demo tasks still contain the same failure.
    act(() => {
      rerender(<Harness project="demo" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })], connected: true });
    });

    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  // Regression: existing historical failures in newly selected projects are baseline history, never notified.
  it("does not notify when switching to a project whose tasks are already failed", async () => {
    useTasksStore.setState({
      tasks: [task({ task_id: "demo-old", status: "succeeded" })],
      connected: true,
    });
    const { rerender } = render(<Harness project="demo" />);

    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      useTasksStore.setState({
        tasks: [task({ task_id: "other-old", project_name: "other", status: "failed" })],
        connected: true,
      });
    });

    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  // Regression: initial polls with no tasks must establish baselines too, or later between-poll rapid failures first
  // observed failed never reach isFreshFailure with seeded=false, permanently missing notices.
  it("notifies for fast failures in a project whose first poll had no tasks", async () => {
    useTasksStore.setState({ tasks: [], connected: true });
    render(<Harness project="demo" />);
    // No prior tasks, but initial polls established baselines. Later polls first show new tasks already failed.
    act(() => {
      useTasksStore.setState({
        tasks: [task({ task_id: "fast", status: "failed", resource_id: "E1S03" })],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S03" });
  });

  // Regression: switches while connected=false (network instability plus user switches) must not misclassify
  // first successful recovery polls as transition commits, preventing seeding and permanently missing subsequent
  // rapid-failure tasks.
  it("notifies for fast failures after switching project while disconnected", async () => {
    useTasksStore.setState({ tasks: [], connected: true });
    const { rerender } = render(<Harness project="demo" />);

    // Network disconnects.
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    // Switch projects while disconnected; projectName changes but connected=false makes effects return early.
    act(() => {
      rerender(<Harness project="other" />);
    });
    // Network recovers; first successful poll returns empty tasks (other project has none).
    act(() => {
      useTasksStore.setState({ tasks: [], connected: true });
    });
    // Next poll first observes new tasks failed rapidly between polls.
    act(() => {
      useTasksStore.setState({
        tasks: [task({ task_id: "fast-after-reconnect", project_name: "other", status: "failed", resource_id: "E1S04" })],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S04" });
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
