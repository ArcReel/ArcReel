import { describe, expect, it, beforeEach } from "vitest";
import {
  useAppStore,
  useAssistantStore,
  useProjectsStore,
  useTasksStore,
  useUsageStore,
} from "@/stores";
import type { DraftState, TaskItem, TimelineEntry } from "@/types";

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
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
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

    app.triggerScrollTo({ type: "segment", id: "S1", route: "/episodes/1", highlight: true });
    expect(useAppStore.getState().scrollTarget).toEqual(
      expect.objectContaining({
        type: "segment",
        id: "S1",
        route: "/episodes/1",
        highlight: true,
        highlight_style: "flash",
      }),
    );
    const requestId = useAppStore.getState().scrollTarget?.request_id;
    expect(requestId).toBeTruthy();
    app.clearScrollTarget(requestId);
    expect(useAppStore.getState().scrollTarget).toBeNull();

    app.setAssistantToolActivitySuppressed(true);
    expect(useAppStore.getState().assistantToolActivitySuppressed).toBe(true);

    // pushToast 只写 toast，不再副作用写入 workspaceNotifications（issue #351 根因回归）
    app.pushToast("hello");
    expect(useAppStore.getState().toast?.text).toBe("hello");
    expect(useAppStore.getState().toast?.tone).toBe("info");
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
    app.clearToast();
    expect(useAppStore.getState().toast).toBeNull();

    // pushNotification 同时写两者，tone 与 target 正确传递
    app.pushNotification("task failed", "error", {
      target: { type: "segment", id: "S1", route: "/episodes/1" },
    });
    expect(useAppStore.getState().toast).toEqual(
      expect.objectContaining({ text: "task failed", tone: "error" }),
    );
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "task failed",
        tone: "error",
        target: expect.objectContaining({ id: "S1" }),
      }),
    );
    app.clearToast();
    useAppStore.setState({ workspaceNotifications: [] });

    app.pushWorkspaceNotification({
      text: "AI 刚更新了角色「hero」，点击查看",
      target: {
        type: "character",
        id: "hero",
        route: "/characters",
      },
    });
    expect(useAppStore.getState().toast).toBeNull();
    const notification = useAppStore.getState().workspaceNotifications[0];
    expect(notification.target?.id).toBe("hero");
    app.markWorkspaceNotificationRead(notification.id);
    expect(useAppStore.getState().workspaceNotifications[0].read).toBe(true);
    app.removeWorkspaceNotification(notification.id);
    expect(
      useAppStore.getState().workspaceNotifications.some((item) => item.id === notification.id)
    ).toBe(false);

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

    expect(useAppStore.getState().entityRevisions).toEqual({});
    expect(app.getEntityRevision("segment:S1")).toBe(0);
    app.invalidateEntities(["segment:S1", "character:hero", "segment:S1"]);
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("character:hero")).toBe(1);
    app.invalidateAllEntities();
    expect(app.getEntityRevision("segment:S1")).toBe(2);
    expect(app.getEntityRevision("clue:missing")).toBe(1);
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

    tasks.setStats({ queued: 1, running: 1, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 2 });
    expect(useTasksStore.getState().stats.total).toBe(2);

    tasks.setConnected(true);
    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("updates projects store fields", () => {
    const projects = useProjectsStore.getState();

    projects.setProjects([{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, status: {} }]);
    expect(useProjectsStore.getState().projects).toHaveLength(1);

    projects.setProjectsLoading(true);
    expect(useProjectsStore.getState().projectsLoading).toBe(true);

    projects.setCurrentProject("demo", {
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: {},
      scenes: {},
      props: {},
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
        project_name: "demo",
        title: "Session 1",
        status: "idle",
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ]);
    assistant.setCurrentSessionId("s1");
    assistant.setSessionsLoading(true);
    assistant.setEntries([{ seq: 0, type: "user", content: [{ type: "text", text: "hi" }] }]);
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

  it("setEntries merges by seq instead of overwriting newer local entries", () => {
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    // 发送响应先落 seq 2；迟到的冷读整帧只有 seq 0-1
    assistant.appendEntry({ seq: 2, type: "user", uuid: "sent", content: [{ type: "text", text: "新" }] });
    assistant.setEntries([
      { seq: 0, type: "user", uuid: "a", content: [{ type: "text", text: "旧1" }] },
      { seq: 1, type: "assistant", uuid: "b", content: [{ type: "text", text: "旧2" }] },
    ]);
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0, 1, 2]);
  });

  it("setDraftSnapshot restores accumulated tool JSON so later suffix deltas parse", () => {
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    assistant.setDraftSnapshot(
      {
        message_id: "msg_1",
        content: [{ type: "tool_use", id: "tu-1", name: "Write", input: {} }],
        rev: 5,
        tool_json: { 0: '{"path": "a' },
      },
      5,
    );
    assistant.applyDelta({
      message_id: "msg_1",
      delta_type: "input_json_delta",
      block_index: 0,
      rev: 6,
      partial_json: '.txt"}',
    });
    const draft = useAssistantStore.getState().draft;
    expect(draft?.content[0].input).toEqual({ path: "a.txt" });
  });

  it("does not throw when a draft payload from the network omits content (SSE boundary is cast, not validated)", () => {
    // useAssistantSession 对 SSE draft 事件做的是 `as DraftState` 类型断言，
    // 无运行时校验；后端载荷若缺 content 字段，这里应兜底为空数组而非崩溃。
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    const malformed = { message_id: "msg_1", rev: 1 } as unknown as DraftState;
    expect(() => assistant.setDraftSnapshot(malformed, 1)).not.toThrow();
    expect(useAssistantStore.getState().draft?.content).toEqual([]);
    expect(useAssistantStore.getState().draftTurn).toBeNull();
  });

  it("does not treat a fresh draft as replaced by a stale committed message_id after a full setState reset", () => {
    const assistant = useAssistantStore.getState();
    // 先提交一条权威 assistant 条目（message_id 进入替换索引）
    assistant.resetTimeline();
    assistant.appendEntry({
      seq: 0,
      type: "assistant",
      message_id: "msg_1",
      uuid: "a-1",
      content: [{ type: "text", text: "上一会话" }],
    });
    // 整帧 setState 重置（绕过 store action，等价于测试 harness 的 reset）
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    // 新会话复用同一 message_id 的 draft：替换索引应按新（空）entries 自愈，
    // draftTurn 不被上一会话的陈旧 message_id 误判为已替换
    useAssistantStore.getState().setDraftSnapshot(
      { message_id: "msg_1", content: [{ type: "text", text: "新会话草稿" }], rev: 1 },
      1,
    );
    expect(useAssistantStore.getState().draftTurn?.content[0].text).toBe("新会话草稿");
  });

  it("self-heals the incremental projector after a full setState reset even when the next cohort reuses stale head/tail entry object references", () => {
    // 增量投影器自身的前缀延续判定只比对 entries 首尾元素引用（O(1) 设计，
    // 不逐一比对中间元素）。若 store 层没有额外按整个 entries 容器引用自愈，
    // 绕过 resetTimeline 的整帧重置后，只要"新"cohort 复用了上一会话的
    // 首尾条目对象引用（例如测试间共享的 fixture 常量），投影器会误判为
    // "前缀延续"，永远不会折叠中段条目的新内容，turns 停留在上一会话的陈旧值。
    const entryX: TimelineEntry = { seq: 0, type: "user", uuid: "x", content: [{ type: "text", text: "开头" }] };
    const entryA: TimelineEntry = { seq: 2, type: "user", uuid: "a", content: [{ type: "text", text: "结尾" }] };
    const entryMid1: TimelineEntry = { seq: 1, type: "user", uuid: "m1", content: [{ type: "text", text: "会话一中段" }] };
    const entryMid2: TimelineEntry = { seq: 1, type: "user", uuid: "m2", content: [{ type: "text", text: "会话二中段" }] };

    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    assistant.setEntries([entryX, entryMid1, entryA]);
    expect(useAssistantStore.getState().turns).toHaveLength(3);

    // 整帧 setState 重置（绕过 resetTimeline action）
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);

    // "新会话"首尾复用同一对条目对象引用，仅中段条目是新对象
    useAssistantStore.getState().setEntries([entryX, entryMid2, entryA]);
    const turns = useAssistantStore.getState().turns;
    expect(turns).toHaveLength(3);
    expect(turns.map((t) => t.content[0].text)).toEqual(["开头", "会话二中段", "结尾"]);
  });

  it("keeps the incremental projector instance alive across appendEntry calls instead of rebuilding it every time", () => {
    // store 层的 projectorSource 自愈检查若拿"即将写入的新数组"和"上一次
    // 记录值"比对，二者引用恒不相等（每次 append 都会重新构造数组），会
    // 导致每次追加都重建 projector、对全部历史条目重新深拷贝——退化为
    // O(n²) 全量重放，正是本 PR 要消除的问题。
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    const original = globalThis.structuredClone;
    let cloneCalls = 0;
    globalThis.structuredClone = ((v: unknown) => {
      cloneCalls++;
      return original(v);
    }) as typeof structuredClone;
    try {
      for (let i = 0; i < 5; i++) {
        assistant.appendEntry({
          seq: i,
          type: "assistant",
          uuid: `a-${i}`,
          content: [{ type: "text", text: `消息${i}` }],
        });
      }
      cloneCalls = 0;
      assistant.appendEntry({
        seq: 5,
        type: "assistant",
        uuid: "a-5",
        content: [{ type: "text", text: "消息5" }],
      });
      // 只深拷贝新追加的这一条；若 projector 被重建，前 5 条也会被重新克隆。
      expect(cloneCalls).toBe(1);
    } finally {
      globalThis.structuredClone = original;
    }
  });

  describe("ProjectsStore fingerprints", () => {
    it("should store and retrieve asset fingerprints", () => {
      const { updateAssetFingerprints, getAssetFingerprint } = useProjectsStore.getState();
      updateAssetFingerprints({ "storyboards/scene_E1S01.png": 1710288000 });
      expect(getAssetFingerprint("storyboards/scene_E1S01.png")).toBe(1710288000);
    });

    it("should merge fingerprints on update", () => {
      const { updateAssetFingerprints, getAssetFingerprint } = useProjectsStore.getState();
      updateAssetFingerprints({ "a.png": 100 });
      updateAssetFingerprints({ "b.png": 200 });
      expect(getAssetFingerprint("a.png")).toBe(100);
      expect(getAssetFingerprint("b.png")).toBe(200);
    });

    it("should return null for unknown paths", () => {
      expect(useProjectsStore.getState().getAssetFingerprint("unknown")).toBeNull();
    });

    it("should set fingerprints from project API response", () => {
      useProjectsStore.getState().setCurrentProject("demo", {} as any, {}, { "storyboards/x.png": 999 });
      expect(useProjectsStore.getState().getAssetFingerprint("storyboards/x.png")).toBe(999);
    });
  });

  it("updates usage store filters, pagination and result payloads", () => {
    const usage = useUsageStore.getState();

    usage.setProjects(["demo", "demo-2"]);
    usage.setFilters({ project_name: "demo", media_type: "image", status: "ok" });
    usage.setStats({
      total_cost: 12.34,
      cost_by_currency: { USD: 12.34 },
      image_count: 5,
      video_count: 2,
      text_count: 0,
      audio_count: 0,
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
          cost_amount: 0.5,
          currency: "USD",
          provider: "gemini",
          output_path: "/tmp/out.png",
          resolution: "1080x1920",
          duration_seconds: null,
          duration_ms: 1200,
          error_message: null,
          started_at: "2026-02-01T00:00:00Z",
          created_at: "2026-02-01T00:00:00Z",
          usage_tokens: null,
          input_tokens: null,
          output_tokens: null,
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
