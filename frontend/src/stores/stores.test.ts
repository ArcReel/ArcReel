import { describe, expect, it, beforeEach } from "vitest";
import {
  useAppStore,
  useAssistantStore,
  useProjectsStore,
  useTasksStore,
} from "@/stores";
import type { DraftState, TimelineEntry } from "@/types";

function resetAllStores(): void {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAssistantStore.setState(useAssistantStore.getInitialState(), true);
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
}

describe("stores", () => {
  beforeEach(() => {
    resetAllStores();
  });

  it("updates app store state and counters", () => {
    const app = useAppStore.getState();

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

    app.pushToast("hello");
    expect(useAppStore.getState().toast?.text).toBe("hello");
    expect(useAppStore.getState().toast?.tone).toBe("info");
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
    useAppStore.setState({ toast: null });

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
    useAppStore.setState({ toast: null, workspaceNotifications: [] });

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

    app.setUsagePanelOpen(true);
    expect(useAppStore.getState().usagePanelOpen).toBe(true);

    expect(useAppStore.getState().entityRevisions).toEqual({});
    expect(app.getEntityRevision("segment:S1")).toBe(0);
    app.invalidateEntities(["segment:S1", "character:hero", "segment:S1"]);
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("character:hero")).toBe(1);
    app.invalidateAllEntities();
    expect(app.getEntityRevision("segment:S1")).toBe(2);
    expect(app.getEntityRevision("clue:missing")).toBe(1);
  });

  it("updates projects store fields", () => {
    const projects = useProjectsStore.getState();

    projects.setProjects([
      { name: "demo", title: "Demo", style: "Anime", thumbnail: null, status: {}, last_activity_at: null },
    ]);
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

  it("resetTimeline rebuilds the timeline and drops the in-place edit state", () => {
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    assistant.setEntries([
      { seq: 0, type: "user", uuid: "old-anchor", content: [{ type: "text", text: "原会话消息" }] },
    ]);
    assistant.setEditingTurnUuid("old-anchor");

    assistant.resetTimeline();

    const state = useAssistantStore.getState();
    expect(state.entries).toEqual([]);
    expect(state.turns).toEqual([]);
    expect(state.draftTurn).toBeNull();
    expect(state.editingTurnUuid).toBeNull();
  });

  it("setEntries merges by seq instead of overwriting newer local entries", () => {
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
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
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    const malformed = { message_id: "msg_1", rev: 1 } as unknown as DraftState;
    expect(() => assistant.setDraftSnapshot(malformed, 1)).not.toThrow();
    expect(useAssistantStore.getState().draft?.content).toEqual([]);
    expect(useAssistantStore.getState().draftTurn).toBeNull();
  });

  it("does not treat a fresh draft as replaced by a stale committed message_id after a full setState reset", () => {
    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    assistant.appendEntry({
      seq: 0,
      type: "assistant",
      message_id: "msg_1",
      uuid: "a-1",
      content: [{ type: "text", text: "上一会话" }],
    });
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useAssistantStore.getState().setDraftSnapshot(
      { message_id: "msg_1", content: [{ type: "text", text: "新会话草稿" }], rev: 1 },
      1,
    );
    expect(useAssistantStore.getState().draftTurn?.content[0].text).toBe("新会话草稿");
  });

  it("self-heals the incremental projector after a full setState reset even when the next cohort reuses stale head/tail entry object references", () => {
    const entryX: TimelineEntry = { seq: 0, type: "user", uuid: "x", content: [{ type: "text", text: "开头" }] };
    const entryA: TimelineEntry = { seq: 2, type: "user", uuid: "a", content: [{ type: "text", text: "结尾" }] };
    const entryMid1: TimelineEntry = { seq: 1, type: "user", uuid: "m1", content: [{ type: "text", text: "会话一中段" }] };
    const entryMid2: TimelineEntry = { seq: 1, type: "user", uuid: "m2", content: [{ type: "text", text: "会话二中段" }] };

    const assistant = useAssistantStore.getState();
    assistant.resetTimeline();
    assistant.setEntries([entryX, entryMid1, entryA]);
    expect(useAssistantStore.getState().turns).toHaveLength(3);

    useAssistantStore.setState(useAssistantStore.getInitialState(), true);

    useAssistantStore.getState().setEntries([entryX, entryMid2, entryA]);
    const turns = useAssistantStore.getState().turns;
    expect(turns).toHaveLength(3);
    expect(turns.map((t) => t.content[0].text)).toEqual(["开头", "会话二中段", "结尾"]);
  });

  it("keeps the incremental projector instance alive across appendEntry calls instead of rebuilding it every time", () => {
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
});
