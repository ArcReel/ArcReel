import { seedTaskState } from "@/test/task-state";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";
import { useTasksStore } from "@/stores/tasks-store";
import { API } from "@/api";
import { useItemLocationsStore } from "@/stores/item-locations-store";
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
    seedTaskState({ tasks: [], connected: true });
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
  });

  afterEach(async () => { await i18n.changeLanguage("zh"); });

  it.each(["text_drama_script_plan", "text_narration_script_plan", "text_reference_script_plan"])("notifies with the English summary and no Agent instructions for %s", async (taskType) => {
    await i18n.changeLanguage("en");
    const summary = "The output has 5 violations and was saved as a draft. Edit the draft or ask AI to repair it.";
    seedTaskState({ tasks: [task({ task_type: taskType, resource_id: "episode-1" })] });
    render(<Harness project="demo" />);
    act(() => { seedTaskState({ tasks: [task({ task_type: taskType, resource_id: "episode-1", status: "failed", error_code: "draft_generation_invalid", error_params: { count: 5 }, error_message: summary, error_detail: "中文报告：open_draft；patch_draft；base_revision" })] }); });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const note = useAppStore.getState().workspaceNotifications[0];
    expect(note.text).toContain(summary);
    expect(note.text).not.toMatch(/open_draft|patch_draft|base_revision|中文报告/);
  });

  it("pushes one clickable notification when a task transitions to failed", async () => {
    seedTaskState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);

    act(() => {
      seedTaskState({ tasks: [task({ status: "failed" })] });
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
    seedTaskState({ tasks: [task({ status: "running" }), task({ task_id: "t2", status: "running" })] });
    render(<Harness project="demo" />);

    act(() => {
      seedTaskState({
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
    seedTaskState({ tasks: [task({ status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("notifies for a brand-new task that appears already-failed after the baseline", async () => {
    seedTaskState({ tasks: [task({ task_id: "old", status: "failed" })] });
    render(<Harness project="demo" />);
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
    act(() => {
      seedTaskState({
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
    seedTaskState({ tasks: [task({ project_name: "other", status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({ tasks: [task({ project_name: "other", status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("notifies only once for the same failed task across repeated updates", async () => {
    seedTaskState({ tasks: [task({ status: "running" })] });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    act(() => {
      seedTaskState({ tasks: [task({ status: "failed" })] });
    });
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  it("does not re-notify historical failed tasks when leaving and returning to the project", async () => {
    seedTaskState({ tasks: [task({ status: "running" })], connected: true });
    const { rerender } = render(<Harness project="demo" />);
    act(() => {
      seedTaskState({ tasks: [task({ status: "failed" })] });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));

    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      seedTaskState({ tasks: [], connected: true });
    });

    act(() => {
      rerender(<Harness project="demo" />);
    });
    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      seedTaskState({ tasks: [task({ status: "failed" })], connected: true });
    });

    expect(useAppStore.getState().workspaceNotifications).toHaveLength(1);
  });

  it("does not notify when switching to a project whose tasks are already failed", async () => {
    seedTaskState({
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
      seedTaskState({
        tasks: [task({ task_id: "other-old", project_name: "other", status: "failed" })],
        connected: true,
      });
    });

    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
  });

  it("notifies for fast failures in a project whose first poll had no tasks", async () => {
    seedTaskState({ tasks: [], connected: true });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({
        tasks: [task({ task_id: "fast", status: "failed", resource_id: "E1S03" })],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S03" });
  });

  it("notifies for fast failures after switching project while disconnected", async () => {
    seedTaskState({ tasks: [], connected: true });
    const { rerender } = render(<Harness project="demo" />);

    act(() => {
      useTasksStore.setState({ connected: false });
    });
    act(() => {
      rerender(<Harness project="other" />);
    });
    act(() => {
      seedTaskState({ tasks: [], connected: true });
    });
    act(() => {
      seedTaskState({
        tasks: [task({ task_id: "fast-after-reconnect", project_name: "other", status: "failed", resource_id: "E1S04" })],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].target).toMatchObject({ id: "E1S04" });
  });

  it("builds a reference_unit target for reference_video failures", async () => {
    seedTaskState({
      tasks: [task({ task_id: "r1", task_type: "reference_video", resource_id: "E1U1", status: "running" })],
    });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({
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

  it("pushes a warning notification pointing to the first failed unit when prompt authoring partially completes", async () => {
    seedTaskState({
      tasks: [
        task({
          task_id: "pa-1",
          task_type: "text_episode_script",
          resource_id: "episode-1",
          status: "running",
        }),
      ],
    });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({
        tasks: [
          task({
            task_id: "pa-1",
            task_type: "text_episode_script",
            resource_id: "episode-1",
            status: "succeeded",
            result: {
              message: "部分完成",
              outcome: "partial",
              written_ids: ["E1U1"],
              failed_units: [{ unit_id: "E1U2", code: "missing_unit", message: "模型漏返单元" }],
            },
          }),
        ],
      });
    });
    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    const note = useAppStore.getState().workspaceNotifications[0];
    expect(note.tone).toBe("warning");
    expect(note.text).toBe("「E1」提示词编写部分完成：已写回 1 个单元，1 个单元未写回");
    expect(note.target).toEqual({
      type: "reference_unit",
      id: "E1U2",
      route: "/episodes/1",
    });
  });

  it("formats failed task directly from top-level position without currentScripts or locateItems", async () => {
    useItemLocationsStore.getState().reset(null);
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "Demo",
        content_mode: "narration",
        style: "",
        characters: {},
        episodes: [{ episode: 2, title: "雨夜", script_file: "scripts/episode_2.json" }],
      },
      currentScripts: {},
    });
    const locateSpy = vi.spyOn(API, "locateItems");
    seedTaskState({
      tasks: [task({ task_id: "pos-1", task_type: "storyboard", resource_id: "E2S03", episode: 2, item_id: "E2S03", position: 3, status: "running" })],
    });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({
        tasks: [task({ task_id: "pos-1", task_type: "storyboard", resource_id: "E2S03", episode: 2, item_id: "E2S03", position: 3, status: "failed", error_message: "boom" })],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(useAppStore.getState().workspaceNotifications[0].text).toContain("雨夜 · 3");
    expect(locateSpy).not.toHaveBeenCalled();
  });

  it("falls back to locateItems for legacy failed tasks lacking position and resource_ref", async () => {
    useItemLocationsStore.getState().reset(null);
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "Demo",
        content_mode: "narration",
        style: "",
        characters: {},
        episodes: [{ episode: 2, title: "雨夜", script_file: "scripts/episode_2.json" }],
      },
      currentScripts: {},
    });
    const locateSpy = vi.spyOn(API, "locateItems").mockResolvedValue({
      items: [{ episode: 2, item_id: "E2S04", position: 4, status: "found" }],
    });
    seedTaskState({
      tasks: [task({ task_id: "leg-1", task_type: "storyboard", resource_id: "E2S04", status: "running" })],
    });
    render(<Harness project="demo" />);
    act(() => {
      seedTaskState({
        tasks: [task({ task_id: "leg-1", task_type: "storyboard", resource_id: "E2S04", status: "failed", error_message: "boom" })],
      });
    });

    await waitFor(() => expect(useAppStore.getState().workspaceNotifications).toHaveLength(1));
    expect(locateSpy).toHaveBeenCalledWith("demo", ["E2S04"], expect.anything());
    expect(useAppStore.getState().workspaceNotifications[0].text).toContain("雨夜 · 4");
  });
});
