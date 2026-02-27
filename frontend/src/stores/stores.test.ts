import { describe, expect, it, beforeEach } from "vitest";
import {
  useAppStore,
  useAssistantStore,
  useProjectsStore,
  useTasksStore,
  useUsageStore,
} from "@/stores";
import type { TaskItem } from "@/types";

function resetAllStores(): void {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAssistantStore.setState(useAssistantStore.getInitialState(), true);
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
  useUsageStore.setState(useUsageStore.getInitialState(), true);
}

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "segment-1",
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    source: "webui",
    queued_at: "2026-02-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

describe("stores", () => {
  beforeEach(() => {
    resetAllStores();
  });

  it("updates app store state and counters", () => {
    const app = useAppStore.getState();

    app.setFocusedContext({ type: "character", id: "hero" });
    expect(useAppStore.getState().focusedContext).toEqual({
      type: "character",
      id: "hero",
    });

    app.triggerScrollTo({ type: "segment", id: "S1", highlight: true });
    expect(useAppStore.getState().scrollTarget).toEqual({
      type: "segment",
      id: "S1",
      highlight: true,
    });
    app.clearScrollTarget();
    expect(useAppStore.getState().scrollTarget).toBeNull();

    app.pushToast("hello");
    expect(useAppStore.getState().toast?.text).toBe("hello");
    expect(useAppStore.getState().toast?.tone).toBe("info");
    app.clearToast();
    expect(useAppStore.getState().toast).toBeNull();

    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    app.toggleAssistantPanel();
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
    app.setAssistantPanelOpen(true);
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);

    app.setTaskHudOpen(true);
    expect(useAppStore.getState().taskHudOpen).toBe(true);

    expect(useAppStore.getState().sourceFilesVersion).toBe(0);
    app.invalidateSourceFiles();
    expect(useAppStore.getState().sourceFilesVersion).toBe(1);
  });

  it("upserts tasks by task_id and updates task stats", () => {
    const tasks = useTasksStore.getState();
    const first = makeTask();
    const updated = makeTask({ status: "running", updated_at: "2026-02-01T00:01:00Z" });
    const second = makeTask({ task_id: "task-2" });

    tasks.upsertTask(first);
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().tasks[0].status).toBe("queued");

    tasks.upsertTask(updated);
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().tasks[0].status).toBe("running");

    tasks.upsertTask(second);
    expect(useTasksStore.getState().tasks).toHaveLength(2);
    expect(useTasksStore.getState().tasks[0].task_id).toBe("task-2");

    tasks.setStats({ queued: 1, running: 1, succeeded: 0, failed: 0, total: 2 });
    expect(useTasksStore.getState().stats.total).toBe(2);

    tasks.setConnected(true);
    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("updates projects store fields", () => {
    const projects = useProjectsStore.getState();

    projects.setProjects([{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, progress: {}, current_phase: "start" }]);
    expect(useProjectsStore.getState().projects).toHaveLength(1);

    projects.setProjectsLoading(true);
    expect(useProjectsStore.getState().projectsLoading).toBe(true);

    projects.setCurrentProject("demo", {
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: {},
      clues: {},
    });
    expect(useProjectsStore.getState().currentProjectName).toBe("demo");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo");

    projects.setProjectDetailLoading(true);
    expect(useProjectsStore.getState().projectDetailLoading).toBe(true);

    projects.setShowCreateModal(true);
    expect(useProjectsStore.getState().showCreateModal).toBe(true);

    projects.setCreatingProject(true);
    expect(useProjectsStore.getState().creatingProject).toBe(true);
  });

  it("updates assistant store state slices", () => {
    const assistant = useAssistantStore.getState();

    assistant.setSessions([
      {
        id: "s1",
        sdk_session_id: null,
        project_name: "demo",
        title: "Session 1",
        status: "idle",
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ]);
    assistant.setCurrentSessionId("s1");
    assistant.setSessionsLoading(true);
    assistant.setTurns([{ type: "user", content: [{ type: "text", text: "hi" }] }]);
    assistant.setDraftTurn({ type: "assistant", content: [{ type: "text", text: "draft" }] });
    assistant.setMessagesLoading(true);
    assistant.setInput("hello");
    assistant.setSending(true);
    assistant.setInterrupting(true);
    assistant.setError("err");
    assistant.setSessionStatus("running");
    assistant.setSessionStatusDetail("busy");
    assistant.setPendingQuestion({
      question_id: "q1",
      questions: [{ question: "?", options: [{ label: "a", description: "b" }], multiSelect: false }],
    });
    assistant.setAnsweringQuestion(true);
    assistant.setSkills([{ name: "x", description: "y", scope: "project", path: "/tmp/x" }]);
    assistant.setSkillsLoading(true);
    assistant.setCurrentProject("demo");
    assistant.setIsDraftSession(true);

    const state = useAssistantStore.getState();
    expect(state.currentSessionId).toBe("s1");
    expect(state.turns).toHaveLength(1);
    expect(state.input).toBe("hello");
    expect(state.sessionStatus).toBe("running");
    expect(state.skills).toHaveLength(1);
    expect(state.isDraftSession).toBe(true);
  });

  it("updates usage store filters, pagination and result payloads", () => {
    const usage = useUsageStore.getState();

    usage.setProjects(["demo", "demo-2"]);
    usage.setFilters({ project_name: "demo", media_type: "image", status: "ok" });
    usage.setStats({
      total_cost: 12.34,
      image_count: 5,
      video_count: 2,
      failed_count: 1,
      total_count: 8,
    });
    usage.setCalls(
      [
        {
          id: "1",
          project_name: "demo",
          call_type: "image",
          model: "model-x",
          status: "succeeded",
          cost_usd: 0.5,
          output_path: "/tmp/out.png",
          resolution: "1080x1920",
          duration_seconds: null,
          duration_ms: 1200,
          error_message: null,
          started_at: "2026-02-01T00:00:00Z",
          created_at: "2026-02-01T00:00:00Z",
        },
      ],
      1,
    );
    usage.setPage(2);
    usage.setLoading(true);

    const state = useUsageStore.getState();
    expect(state.projects).toEqual(["demo", "demo-2"]);
    expect(state.filters.project_name).toBe("demo");
    expect(state.stats?.total_cost).toBe(12.34);
    expect(state.calls).toHaveLength(1);
    expect(state.total).toBe(1);
    expect(state.page).toBe(2);
    expect(state.loading).toBe(true);
  });
});
