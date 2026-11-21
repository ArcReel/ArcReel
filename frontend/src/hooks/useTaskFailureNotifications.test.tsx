import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
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
    // connected=true：模拟首个成功 poll 已完成、基线可建立。
    useTasksStore.setState({ tasks: [], connected: true });
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
  });

  afterEach(async () => { await i18n.changeLanguage("zh"); });

  it.each(["text_drama_script_plan", "text_narration_script_plan", "text_reference_script_plan"])("notifies with the English summary and no Agent instructions for %s", async (taskType) => {
    await i18n.changeLanguage("en");
    const summary = "The output has 5 violations and was saved as a draft. Edit the draft or ask AI to repair it.";
    useTasksStore.setState({ tasks: [task({ task_type: taskType, resource_id: "episode-1" })] });
    render(<Harness project="demo" />);
    act(() => { useTasksStore.setState({ tasks: [task({ task_type: taskType, resource_id: "episode-1", status: "failed", error_code: "draft_generation_invalid", error_params: { count: 5 }, error_message: summary, error_detail: "中文报告：open_draft；patch_draft；base_revision" })] }); });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const note = useAppStore.getState().workspaceNotifications[0];
    expect(note.text).toContain(summary);
    expect(note.text).not.toMatch(/open_draft|patch_draft|base_revision|中文报告/);
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

  // 3 秒轮询下，任务可能在两次 poll 之间直接失败，首次被前端观测到时已是 failed。
  // 基线建立后才首次出现的这类新任务应通知，否则后台快速失败会被漏报。
  it("notifies for a brand-new task that appears already-failed after the baseline", async () => {
    useTasksStore.setState({ tasks: [task({ task_id: "old", status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
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

  it("notifies only once for the same failed task across repeated updates", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  // 回归：切换项目（A→B→A）模拟用户离开再回到同一项目。useTasksSSE 的真实时序是
  // cleanup setConnected(false) → 新 poll → setTasks(新项目) + setConnected(true)。
  // 切回 A 后已通过 transition 通知过的历史 failed 不应被当作 fresh failure 再推一遍。
  it("does not re-notify historical failed tasks when leaving and returning to the project", async () => {
    useTasksStore.setState({ tasks: [task({ status: "running" })], connected: true });
    const { rerender } = render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));

    // 切到另一个项目：projectName 先变，然后 useTasksSSE cleanup+setup 模拟
    // setConnected(false) → setTasks(空) + setConnected(true)
    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      useTasksStore.setState({ tasks: [], connected: true });
    });

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

  // 回归：项目首轮 poll 即使无任务也应建立基线，否则后续 task 在两 poll 间快速失败、
  // 首次被观测即 failed 时，因 seeded=false 永远走不进 isFreshFailure 通道，永久漏报。
  it("notifies for fast failures in a project whose first poll had no tasks", async () => {
    useTasksStore.setState({ tasks: [], connected: true });
    render(<Harness project="demo" />);
    act(() => {
      useTasksStore.setState({
        tasks: [task({ task_id: "fast", status: "failed", resource_id: "E1S03" })],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S03" });
  });

  // 回归：connected=false 期间切换项目（如网络抖动 + 用户切到别的项目），随后网络
  // 恢复时第一个成功 poll 不能被误判为过渡 commit，否则永远不 seed，下一轮快速失败
  // 任务将被永久漏报。
  it("notifies for fast failures after switching project while disconnected", async () => {
    useTasksStore.setState({ tasks: [], connected: true });
    const { rerender } = render(<Harness project="demo" />);

    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      useTasksStore.setState({ tasks: [], connected: true });
    });
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
