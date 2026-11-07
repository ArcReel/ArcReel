import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { AgentFailureError, API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import type {
  AcceptedMessageResponse,
  EntriesResponse,
  PendingQuestion,
  QueuedMessage,
  QueuedMessageWithdrawalResponse,
  QueuedMessageResendResponse,
  SessionMeta,
  SkillInfo,
  TimelineEntry,
} from "@/types";
import { useAssistantSession } from "./useAssistantSession";
import { createDeferred } from "@/test/deferred";
import { FakeSseStream } from "@/test/fakeSseStream";

function makeSession(id: string, status: SessionMeta["status"]): SessionMeta {
  return {
    id,
    project_name: "demo",
    title: id,
    status,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  };
}

function makePendingQuestion(questionId: string = "q-1"): PendingQuestion {
  return {
    question_id: questionId,
    questions: [
      {
        header: "输出",
        question: "输出格式是什么？",
        multiSelect: false,
        options: [
          { label: "摘要", description: "简洁输出" },
          { label: "详细", description: "完整说明" },
        ],
      },
    ],
  };
}

function makeEntriesResponse(overrides: Partial<EntriesResponse> = {}): EntriesResponse {
  return {
    session_id: "session-1",
    status: "idle",
    entries: [],
    draft: null,
    draft_rev: 0,
    ...overrides,
  };
}

function userEntry(seq: number, text: string): TimelineEntry {
  return { seq, type: "user", content: [{ type: "text", text }], uuid: `u-${seq}` };
}

function queuedMessage(id: string, text: string): QueuedMessage {
  return { id, content: [{ type: "text", text }], timestamp: "2026-02-01T00:00:00Z", state: "queued" };
}

function mockIdleSession(entries: TimelineEntry[] = []) {
  vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
    sessions: [makeSession("session-1", "idle")],
  });
  vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
  vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries }));
}

describe("useAssistantSession", () => {
  let openStreamSpy: MockInstance<typeof API.openAssistantEntriesStream>;

  /** Arguments to `API.openAssistantEntriesStream` on creation index: sessions and cold-subscription cursors. */
  function streamOptions(index: number) {
    return openStreamSpy.mock.calls[index][0];
  }

  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    FakeSseStream.reset();
    localStorage.clear();
    vi.restoreAllMocks();
    openStreamSpy = vi
      .spyOn(API, "openAssistantEntriesStream")
      .mockImplementation((options) => new FakeSseStream(options.onEvent));
    vi.spyOn(API, "listAssistantSkills").mockResolvedValue({ skills: [] });
  });

  it("loads idle session timeline from the entries endpoint (cold read)", async () => {
    mockIdleSession([userEntry(0, "历史消息"), { seq: 1, type: "assistant", content: [{ type: "text", text: "回复" }], uuid: "a-1" }]);

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().turns).toHaveLength(2);
    });
    expect(useAssistantStore.getState().turns[0].content[0].text).toBe("历史消息");
    // Attach resident entry streams after cold reads, starting after last cold entries.
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    expect(streamOptions(0)).toMatchObject({ sessionId: "session-1", after: 1 });
  });

  it("connects entry stream for running sessions and appends entries in seq order", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    act(() => {
      FakeSseStream.instances[0].emit("entry", userEntry(0, "hello"));
      FakeSseStream.instances[0].emit("entry", {
        seq: 1,
        type: "assistant",
        content: [{ type: "text", text: "hi" }],
        uuid: "a-1",
      });
      // Duplicate seq: dedupe by identity (seq), never compare contents.
      FakeSseStream.instances[0].emit("entry", { ...userEntry(1, "重复"), type: "assistant" });
    });

    const state = useAssistantStore.getState();
    expect(state.entries.map((e) => e.seq)).toEqual([0, 1]);
    expect(state.turns.map((t) => t.type)).toEqual(["user", "assistant"]);
  });

  it("keeps the queued-message tray in step with the stream's snapshot and increments", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];
    const queuedIds = () => useAssistantStore.getState().queuedMessages.map((m) => m.id);

    act(() => {
      stream.emit("queue", { session_id: "session-1", messages: [queuedMessage("u-0", "先改第 3 镜")] });
    });
    expect(queuedIds()).toEqual(["u-0"]);

    act(() => {
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-1", "再改第 4 镜") });
    });
    expect(queuedIds()).toEqual(["u-0", "u-1"]);

    // Agent admits first messages: entries arrive first, then leave trays.
    act(() => {
      stream.emit("entry", userEntry(0, "先改第 3 镜"));
      stream.emit("queue_remove", { session_id: "session-1", id: "u-0" });
    });
    expect(queuedIds()).toEqual(["u-1"]);
    expect(useAssistantStore.getState().entries.map((e) => e.uuid)).toEqual(["u-0"]);
  });

  describe("editing and deleting queued messages", () => {
    async function openRunningSession() {
      vi.spyOn(API, "listAssistantSessions").mockResolvedValue({ sessions: [makeSession("session-1", "running")] });
      vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
      const hook = renderHook(({ projectName }) => useAssistantSession(projectName), {
        initialProps: { projectName: "demo" },
      });
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));
      const stream = FakeSseStream.instances[0];
      act(() => {
        stream.emit("queue", { session_id: "session-1", messages: [queuedMessage("u-1", "第 3 镜改成黄昏")] });
      });
      return { ...hook, stream };
    }

    it("hands the content of an edited message to the input once and drops it from the tray", async () => {
      const { result, stream } = await openRunningSession();
      const withdraw = vi.spyOn(API, "withdrawQueuedMessage").mockResolvedValue({
        session_id: "session-1",
        id: "u-1",
        outcome: "withdrawn",
        message: queuedMessage("u-1", "第 3 镜改成黄昏"),
      });

      await act(async () => {
        await result.current.withdrawQueuedMessage("u-1", "edit");
      });
      // Stream removals may postdate responses; never restore contents twice.
      act(() => {
        stream.emit("queue_remove", {
          session_id: "session-1",
          id: "u-1",
          withdrawn: "edit",
          message: queuedMessage("u-1", "第 3 镜改成黄昏"),
        });
      });

      expect(withdraw).toHaveBeenCalledWith("demo", "session-1", "u-1", "edit");
      expect(useAssistantStore.getState().composerAppends).toEqual([[{ type: "text", text: "第 3 镜改成黄昏" }]]);
      expect(useAssistantStore.getState().queuedMessages).toEqual([]);
    });

    it("sends an unsent message again and shows it queued, leaving the input alone", async () => {
      const { result, stream } = await openRunningSession();
      act(() => {
        stream.emit("queue_upsert", {
          session_id: "session-1",
          message: { ...queuedMessage("u-1", "第 3 镜改成黄昏"), state: "unsent" },
        });
      });
      const resend = vi.spyOn(API, "resendQueuedMessage").mockImplementation(async () => {
        stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-1", "第 3 镜改成黄昏") });
        return { session_id: "session-1", id: "u-1", queued_message: queuedMessage("u-1", "第 3 镜改成黄昏") };
      });

      await act(async () => {
        await result.current.resendQueuedMessage("u-1");
      });

      expect(resend).toHaveBeenCalledWith("demo", "session-1", "u-1");
      expect(useAssistantStore.getState().queuedMessages).toEqual([queuedMessage("u-1", "第 3 镜改成黄昏")]);
      expect(useAssistantStore.getState().composerAppends).toEqual([]);
    });

    it.each(["session", "project"] as const)("returns a late edit to the shared input after switching %s, without changing its tray or error", async (navigation) => {
      const { result, rerender } = await openRunningSession();
      vi.mocked(API.getAssistantSession).mockImplementation(async (_project, sessionId) => ({
        session: makeSession(sessionId, "running"),
      }));
      const delayed = createDeferred<QueuedMessageWithdrawalResponse>();
      vi.spyOn(API, "withdrawQueuedMessage").mockReturnValue(delayed.promise);
      let editing = Promise.resolve();
      act(() => { editing = result.current.withdrawQueuedMessage("u-1", "edit"); });

      if (navigation === "session") {
        await act(async () => { await result.current.switchSession("session-2"); });
      } else {
        rerender({ projectName: "other" });
      }
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(2));
      const nextMessage = queuedMessage("u-2", "当前会话消息");
      act(() => {
        useAssistantStore.getState().setQueuedMessages([nextMessage]);
        useAssistantStore.getState().setError("当前会话错误");
      });
      await act(async () => {
        delayed.resolve({
          session_id: "session-1", id: "u-1", outcome: "withdrawn",
          message: queuedMessage("u-1", "第 3 镜改成黄昏"),
        });
        await editing;
      });

      expect(useAssistantStore.getState().currentSessionId).toBe(navigation === "session" ? "session-2" : "session-1");
      expect(useAssistantStore.getState().composerAppends).toEqual([[{ type: "text", text: "第 3 镜改成黄昏" }]]);
      expect(useAssistantStore.getState().queuedMessages).toEqual([nextMessage]);
      expect(useAssistantStore.getState().error).toBe("当前会话错误");
    });

    it("keeps a later CLI exit ahead of a delayed resend response", async () => {
      const { result, stream } = await openRunningSession();
      const unsent = { ...queuedMessage("u-1", "第 3 镜改成黄昏"), state: "unsent" as const };
      const delayed = createDeferred<QueuedMessageResendResponse>();
      vi.spyOn(API, "resendQueuedMessage").mockReturnValue(delayed.promise);
      act(() => { stream.emit("queue_upsert", { session_id: "session-1", message: unsent }); });
      let resending = Promise.resolve();
      act(() => { resending = result.current.resendQueuedMessage("u-1"); });
      act(() => {
        stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-1", "第 3 镜改成黄昏") });
        stream.emit("queue_upsert", { session_id: "session-1", message: unsent });
      });
      await act(async () => {
        delayed.resolve({ session_id: "session-1", id: "u-1", queued_message: queuedMessage("u-1", "第 3 镜改成黄昏") });
        await resending;
      });
      expect(useAssistantStore.getState().queuedMessages).toEqual([unsent]);
    });

    it.each(["withdraw", "resend", "send-now"] as const)("ignores a late %s error after closing the panel", async (action) => {
      const { result, unmount } = await openRunningSession();
      const delayed = createDeferred<never>();
      vi.spyOn(API, "withdrawQueuedMessage").mockReturnValue(delayed.promise);
      vi.spyOn(API, "resendQueuedMessage").mockReturnValue(delayed.promise);
      vi.spyOn(API, "sendQueuedMessageNow").mockReturnValue(delayed.promise);
      let request = Promise.resolve();
      act(() => {
        request = action === "withdraw" ? result.current.withdrawQueuedMessage("u-1", "edit")
          : action === "resend" ? result.current.resendQueuedMessage("u-1")
          : result.current.sendQueuedMessageNow("u-1");
      });
      unmount();
      await act(async () => {
        delayed.reject(new Error("old request failed"));
        await request;
      });
      expect(useAssistantStore.getState().error).toBeNull();
    });

    it("follows a later cancel back to the input even after sending another message", async () => {
      const { result, stream } = await openRunningSession();
      vi.spyOn(API, "withdrawQueuedMessage").mockResolvedValue({
        session_id: "session-1",
        id: "u-1",
        outcome: "accepted",
        message: null,
      });

      await act(async () => {
        await result.current.withdrawQueuedMessage("u-1", "edit");
      });

      expect(useAppStore.getState().toast?.text).toBe("已被 Agent 接收，这条消息会照常进入对话");
      expect(useAssistantStore.getState().queuedMessages.map((m) => m.id)).toEqual(["u-1"]);
      vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
        session_id: "session-1", status: "accepted", entry: null,
        queued_message: queuedMessage("u-2", "再加一段旁白"),
      });
      await act(async () => { await result.current.sendMessage("再加一段旁白"); });
      // Agent ultimately skipped messages; return them to inputs as edits.
      act(() => {
        stream.emit("queue_remove", {
          session_id: "session-1",
          id: "u-1",
          withdrawn: "edit",
          message: queuedMessage("u-1", "第 3 镜改成黄昏"),
        });
      });
      expect(useAssistantStore.getState().composerAppends).toEqual([[{ type: "text", text: "第 3 镜改成黄昏" }]]);
      expect(useAssistantStore.getState().queuedMessages).toEqual([]);
    });

    it("drops a message another page edited without touching this page's input", async () => {
      const { stream } = await openRunningSession();

      act(() => {
        stream.emit("queue_remove", {
          session_id: "session-1",
          id: "u-1",
          withdrawn: "edit",
          message: queuedMessage("u-1", "第 3 镜改成黄昏"),
        });
      });

      expect(useAssistantStore.getState().queuedMessages).toEqual([]);
      expect(useAssistantStore.getState().composerAppends).toEqual([]);
    });
  });

  it("keeps a pending send locked when the stream reports the previous turn idle", async () => {
    mockIdleSession();
    const pending = createDeferred<AcceptedMessageResponse>();
    const send = vi.spyOn(API, "sendAssistantMessage").mockReturnValue(pending.promise);
    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));
    let first: Promise<boolean> = Promise.resolve(false);
    act(() => {
      first = result.current.sendMessage("补充要求");
    });
    act(() => {
      FakeSseStream.instances[0].emit("status", { session_id: "session-1", status: "completed" });
    });
    expect(useAssistantStore.getState().sending).toBe(true);
    await act(async () => {
      expect(await result.current.sendMessage("补充要求")).toBe(false);
      pending.resolve({ session_id: "session-1", status: "accepted", entry: null, queued_message: queuedMessage("u-1", "补充要求") });
      expect(await first).toBe(true);
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(useAssistantStore.getState().sending).toBe(false);
  });

  it("keeps server queue order when a send response arrives before earlier stream events", async () => {
    mockIdleSession();
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "session-1",
      status: "accepted",
      entry: null,
      queued_message: queuedMessage("u-2", "本页发出的消息"),
    });
    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));

    await act(async () => {
      expect(await result.current.sendMessage("本页发出的消息")).toBe(true);
    });
    act(() => {
      const stream = FakeSseStream.instances[0];
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-1", "另一页先发出的消息") });
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-2", "本页发出的消息") });
    });
    expect(useAssistantStore.getState().queuedMessages.map((message) => message.id)).toEqual(["u-1", "u-2"]);
  });

  it("puts a message sent during a reply into the tray, and a late response does not bring back one already taken", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
    const late = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage")
      .mockResolvedValueOnce({
        session_id: "session-1",
        status: "accepted",
        entry: null,
        queued_message: queuedMessage("u-1", "再改第 4 镜"),
      })
      .mockReturnValueOnce(late.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];
    const queuedIds = () => useAssistantStore.getState().queuedMessages.map((m) => m.id);

    await act(async () => {
      expect(await result.current.sendMessage("再改第 4 镜")).toBe(true);
    });
    act(() => {
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-1", "再改第 4 镜") });
    });
    expect(queuedIds()).toEqual(["u-1"]);
    expect(useAssistantStore.getState().sessionStatus).toBe("running");

    // Second responses arrive late; streams already admitted messages and removed them from queues.
    let sent: Promise<boolean> = Promise.resolve(false);
    act(() => {
      sent = result.current.sendMessage("第 5 镜删掉");
    });
    act(() => {
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-2", "第 5 镜删掉") });
      stream.emit("entry", userEntry(2, "第 5 镜删掉"));
      stream.emit("queue_remove", { session_id: "session-1", id: "u-2" });
    });
    await act(async () => {
      late.resolve({
        session_id: "session-1",
        status: "accepted",
        entry: null,
        queued_message: queuedMessage("u-2", "第 5 镜删掉"),
      });
      expect(await sent).toBe(true);
    });

    expect(queuedIds()).toEqual(["u-1"]);
  });

  it("applies draft snapshot and rev-gated deltas, then replaces draft by message_id identity", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    act(() => {
      // Reconnection first-frame snapshots carry accumulated state + rev gates.
      FakeSseStream.instances[0].emit("draft", {
        session_id: "session-1",
        draft: { message_id: "msg_1", content: [{ type: "text", text: "部分" }], rev: 5 },
        rev: 5,
      });
      // Filter gap-redelivered deltas with rev ≤ gates.
      FakeSseStream.instances[0].emit("delta", {
        message_id: "msg_1",
        delta_type: "text_delta",
        block_index: 0,
        rev: 5,
        text: "重复",
      });
      FakeSseStream.instances[0].emit("delta", {
        message_id: "msg_1",
        delta_type: "text_delta",
        block_index: 0,
        rev: 6,
        text: "内容",
      });
    });

    expect(useAssistantStore.getState().draftTurn?.content[0].text).toBe("部分内容");

    act(() => {
      // Persisted authoritative same-message_id entries replace drafts exactly by identity.
      FakeSseStream.instances[0].emit("entry", {
        seq: 0,
        type: "assistant",
        message_id: "msg_1",
        content: [{ type: "text", text: "部分内容（权威）" }],
        uuid: "a-1",
      });
    });

    const state = useAssistantStore.getState();
    expect(state.draftTurn).toBeNull();
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].content[0].text).toBe("部分内容（权威）");
  });

  it("clears draft on terminal status without closing the stream, keeps draft when interrupted", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    act(() => {
      FakeSseStream.instances[0].emit("draft", {
        session_id: "session-1",
        draft: { message_id: "msg_1", content: [{ type: "text", text: "被中断的回复" }], rev: 1 },
        rev: 1,
      });
      FakeSseStream.instances[0].emit("status", { status: "interrupted" });
    });

    // Interruptions retain drafts; interrupted contents never log and disappear on refresh.
    expect(useAssistantStore.getState().draftTurn?.content[0].text).toBe("被中断的回复");
    expect(useAssistantStore.getState().sessionStatus).toBe("interrupted");
    expect(FakeSseStream.instances[0].close).not.toHaveBeenCalled();
  });

  it("keeps the stream open after a terminal status, so entries from a later send still show", async () => {
    mockIdleSession([userEntry(0, "第一轮")]);
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "session-1",
      status: "accepted",
      entry: userEntry(1, "第二轮"),
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];

    // Initial and previous-round terminal status deliveries
    act(() => {
      stream.emit("status", { status: "completed" });
    });

    await act(async () => {
      await result.current.sendMessage("第二轮");
    });
    act(() => {
      stream.emit("status", { status: "running" });
      stream.emit("entry", { seq: 2, type: "assistant", content: [{ type: "text", text: "第二轮回复" }], uuid: "a-2" });
      stream.emit("status", { status: "completed" });
      stream.emit("entry", userEntry(3, "自主轮次"));
    });

    expect(FakeSseStream.instances).toHaveLength(1);
    expect(stream.close).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0, 1, 2, 3]);
    expect(useAssistantStore.getState().turns.at(-2)?.content[0].text).toBe("第二轮回复");
    expect(useAssistantStore.getState().sessionStatus).toBe("completed");
  });

  it("keeps the CLI terminal status when it arrives before the send response", async () => {
    mockIdleSession();
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];
    let send: Promise<boolean>;
    act(() => {
      send = result.current.sendMessage("hello");
    });
    act(() => {
      stream.emit("status", { status: "running" });
      stream.emit("entry", userEntry(0, "hello"));
      stream.emit("status", { status: "completed" });
    });
    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });
      expect(await send).toBe(true);
    });

    expect(useAssistantStore.getState().sessionStatus).toBe("completed");
    expect(useAssistantStore.getState().entries).toHaveLength(1);
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(stream.close).not.toHaveBeenCalled();
  });

  it("keeps the CLI terminal status when the queued message left the queue unaccepted before the send response", async () => {
    mockIdleSession();
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];
    let send: Promise<boolean>;
    act(() => {
      send = result.current.sendMessage("hello");
    });
    // CLI exits without admission: messages leave queues without entries; sessions become terminal.
    act(() => {
      stream.emit("status", { status: "running" });
      stream.emit("queue_upsert", { session_id: "session-1", message: queuedMessage("u-0", "hello") });
      stream.emit("queue_remove", { session_id: "session-1", id: "u-0" });
      stream.emit("status", { status: "error" });
    });
    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: null, queued_message: queuedMessage("u-0", "hello") });
      expect(await send).toBe(true);
    });

    expect(useAssistantStore.getState().sessionStatus).toBe("error");
    expect(useAssistantStore.getState().queuedMessages).toEqual([]);
  });

  it("sets running after accepted when only an older terminal snapshot arrived during send", async () => {
    mockIdleSession([userEntry(0, "上一轮")]);
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    act(() => {
      void result.current.sendMessage("hello");
      FakeSseStream.instances[0].emit("status", { status: "completed" });
    });
    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(1, "hello"), queued_message: null });
      await deferred.promise;
    });

    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    expect(FakeSseStream.instances).toHaveLength(1);
  });

  it("writes pendingQuestion from question SSE events", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    act(() => {
      FakeSseStream.instances[0].emit("question", makePendingQuestion());
    });

    expect(useAssistantStore.getState().pendingQuestion?.question_id).toBe("q-1");
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
  });

  it("submits answers successfully and clears pendingQuestion", async () => {
    mockIdleSession();
    const answerSpy = vi
      .spyOn(API, "answerAssistantQuestion")
      .mockResolvedValue({ success: true });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion());
    });

    await act(async () => {
      await result.current.answerQuestion("q-1", { "输出格式是什么？": "摘要" });
    });

    expect(answerSpy).toHaveBeenCalledWith("demo", "session-1", "q-1", {
      "输出格式是什么？": "摘要",
    });
    expect(useAssistantStore.getState().pendingQuestion).toBeNull();
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
  });

  it("keeps pendingQuestion and surfaces errors when answer submission fails", async () => {
    mockIdleSession();
    vi.spyOn(API, "answerAssistantQuestion").mockRejectedValue(new Error("回答失败"));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion());
    });

    await act(async () => {
      await result.current.answerQuestion("q-1", { "输出格式是什么？": "摘要" });
    });

    expect(useAssistantStore.getState().pendingQuestion?.question_id).toBe("q-1");
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
    expect(useAssistantStore.getState().error).toBe("回答失败");
  });

  it("clears pendingQuestion when creating or switching sessions", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [
        makeSession("session-1", "idle"),
        makeSession("session-2", "idle"),
      ],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse());

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion());
      useAssistantStore.getState().setAnsweringQuestion(true);
    });

    await act(async () => {
      result.current.createNewSession();
    });

    expect(useAssistantStore.getState().pendingQuestion).toBeNull();
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion("q-2"));
      useAssistantStore.getState().setAnsweringQuestion(true);
    });

    await act(async () => {
      await result.current.switchSession("session-2");
    });

    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().pendingQuestion).toBeNull();
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
  });

  it("appends the authoritative entry from the send response and connects the entry stream", async () => {
    mockIdleSession();
    const sendSpy = vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "session-1",
      status: "accepted",
      entry: userEntry(0, "hello"),
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let accepted = false;
    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });

    expect(accepted).toBe(true);
    // No locally synthesized messages; response-authoritative entries alone supply timelines.
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0]);
    expect(useAssistantStore.getState().turns).toEqual([
      { type: "user", content: [{ type: "text", text: "hello" }], uuid: "u-0", timestamp: undefined },
    ]);
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    // Sending reuses streams established when sessions opened.
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(streamOptions(0)).toMatchObject({ projectName: "demo", sessionId: "session-1" });
    expect(FakeSseStream.instances[0].close).not.toHaveBeenCalled();
    // Requests carry client_key.
    expect(sendSpy.mock.calls[0][4]).toEqual(expect.any(String));
  });

  it("keeps timeline unchanged on send failure and reuses the idempotency key on retry", async () => {
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败"))
      .mockResolvedValueOnce({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let accepted = true;
    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });

    // Failure: no synthetic rollback; timelines/status unchanged, callers retain inputs.
    expect(accepted).toBe(false);
    expect(useAssistantStore.getState().sending).toBe(false);
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(useAssistantStore.getState().turns).toEqual([]);
    expect(useAssistantStore.getState().error).toBe("发送失败");

    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });

    expect(accepted).toBe(true);
    // Same-content retries reuse idempotency keys; server key dedupe prevents duplicates.
    expect(sendSpy).toHaveBeenCalledTimes(2);
    expect(sendSpy.mock.calls[1][4]).toBe(sendSpy.mock.calls[0][4]);
  });

  it("stores a startup failure observation separately from generic send errors", async () => {
    mockIdleSession();
    const failure = {
      version: 1,
      phase: "startup" as const,
      timestamp: "2026-07-23T01:02:03Z",
      project_name: "demo",
      session_id: "session-1",
      summary: {
        source: "local_exception",
        type: "ProcessError",
        message: "Claude Code exited before initialization",
      },
      raw: {
        exception_chain: [{ type: "ProcessError", vendor_field: "keep-me" }],
        sdk_stderr: "observed stderr",
      },
    };
    vi.spyOn(API, "sendAssistantMessage").mockRejectedValue(
      new AgentFailureError("Agent 启动失败", failure),
    );

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      expect(await result.current.sendMessage("hello")).toBe(false);
    });

    expect(useAssistantStore.getState().startupFailure).toEqual(failure);
    expect(useAssistantStore.getState().error).toBeNull();
    expect(useAssistantStore.getState().turns).toEqual([]);
  });

  it("uses a fresh idempotency key for different content", async () => {
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败"))
      .mockResolvedValueOnce({ session_id: "session-1", status: "accepted", entry: userEntry(0, "另一条"), queued_message: null });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });
    await act(async () => {
      await result.current.sendMessage("另一条");
    });

    expect(sendSpy.mock.calls[1][4]).not.toBe(sendSpy.mock.calls[0][4]);
  });

  it("uses a fresh idempotency key after switching projects (signature includes project)", async () => {
    // Long-lived singleton panels survive project switches; project A failure caches (clientKey + signatures)
    // survive across projects. Project-aware signatures must generate fresh keys on same-content B retries, never reuse A keys.
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败")) // Project A: fail and cache clientKey.
      .mockResolvedValueOnce({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });

    const { result, rerender } = renderHook(({ p }) => useAssistantSession(p), {
      initialProps: { p: "proj_a" },
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    // Switch projects, retaining the same hook instance and failure-cache refs.
    rerender({ p: "proj_b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(sendSpy).toHaveBeenCalledTimes(2);
    // Cross-project same-content retries: project signatures generate fresh keys, never reuse prior-project cached clientKey.
    expect(sendSpy.mock.calls[1][4]).not.toBe(sendSpy.mock.calls[0][4]);
  });

  it("ignores delayed send completions after switching sessions", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [
        makeSession("session-1", "idle"),
        makeSession("session-2", "idle"),
      ],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({
        session_id: sessionId,
        entries: sessionId === "session-2"
          ? [{ seq: 0, type: "assistant", content: [{ type: "text", text: "session-2" }], uuid: "a-1" }]
          : [],
      }),
    );
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    await act(async () => {
      await result.current.switchSession("session-2");
    });

    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().sending).toBe(false);
    expect(useAssistantStore.getState().turns).toEqual([
      { type: "assistant", content: [{ type: "text", text: "session-2" }], uuid: "a-1", timestamp: undefined },
    ]);

    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });
      await deferred.promise;
    });

    // Late send completion must not pollute switched-session timelines or create original-session streams.
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1", "session-2"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
  });

  it("resolves false for a send invalidated by switching sessions mid-flight", async () => {
    // Version mismatches must return false like failures; callers (AgentCopilot.handleSend)
    // clear inputs based on returns, so incorrect true erases freshly entered contents in newly switched sessions.
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [
        makeSession("session-1", "idle"),
        makeSession("session-2", "idle"),
      ],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let sendResult: boolean | undefined;
    act(() => {
      void result.current.sendMessage("hello").then((accepted) => {
        sendResult = accepted;
      });
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    await act(async () => {
      await result.current.switchSession("session-2");
    });

    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });
      await deferred.promise;
    });

    await waitFor(() => {
      expect(sendResult).toBe(false);
    });
  });

  it("resolves false when currentSessionId changes without going through invalidatePendingSend", async () => {
    // Second defense independent of versions: currentSessionId is globally shared across hook instances,
    // potentially changed externally without invalidatePendingSend. Even matching versions
    // must not return true and clear new-session inputs when response-time currentSessionId differs from sending sessions.
    mockIdleSession();
    const deferred = createDeferred<AcceptedMessageResponse>();
    vi.spyOn(API, "sendAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let sendResult: boolean | undefined;
    act(() => {
      void result.current.sendMessage("hello").then((accepted) => {
        sendResult = accepted;
      });
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    act(() => {
      useAssistantStore.getState().setCurrentSessionId("session-2");
    });

    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello"), queued_message: null });
      await deferred.promise;
    });

    await waitFor(() => {
      expect(sendResult).toBe(false);
    });
  });

  it("resets timeline on project switch so a running session's SSE cold cursor is not polluted by the previous project's residual entries", async () => {
    vi.spyOn(API, "listAssistantSessions").mockImplementation(async (projectName) => ({
      sessions: [makeSession(projectName === "project-a" ? "session-a" : "session-b", "running")],
    }));
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "running"),
    }));

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    // Project A running sessions create cold streams and receive residual entries.
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    act(() => {
      FakeSseStream.instances[0].emit("entry", userEntry(0, "A-0"));
      FakeSseStream.instances[0].emit("entry", {
        seq: 1,
        type: "assistant",
        content: [{ type: "text", text: "A-1" }],
        uuid: "a-1",
      });
    });
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0, 1]);

    // Switch to project B, also running sessions.
    rerender({ projectName: "project-b" });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
    });
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(2);
    });

    // Cold cursors derive from reset empty entries (after=-1, equivalent to start),
    // unaffected by A's residual maximum seq.
    expect(streamOptions(1).after).toBe(-1);
    // Timelines contain no A entries.
    expect(useAssistantStore.getState().entries).toEqual([]);
    expect(useAssistantStore.getState().turns).toEqual([]);
  });

  it("resets timeline when switching to a project that has no sessions", async () => {
    vi.spyOn(API, "listAssistantSessions").mockImplementation(async (projectName) => ({
      sessions: projectName === "project-a" ? [makeSession("session-a", "idle")] : [],
    }));
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-a", "idle") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(
      makeEntriesResponse({ entries: [userEntry(0, "A-0")] }),
    );

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    // Project A idle sessions cold-read one historical entry.
    await waitFor(() => {
      expect(useAssistantStore.getState().turns).toHaveLength(1);
    });

    // Switching to sessionless projects also resets timelines through no-session initialization.
    rerender({ projectName: "project-b" });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBeNull();
    });
    expect(useAssistantStore.getState().entries).toEqual([]);
    expect(useAssistantStore.getState().turns).toEqual([]);
  });

  it("reuses an open stream for the same session and resumes from the last seq once it is closed", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({ session_id: "session-1", status: "accepted", entry: null, queued_message: null });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    // Initial cold subscriptions start at beginning, with empty entries.
    expect(streamOptions(0).after).toBe(-1);

    act(() => {
      FakeSseStream.instances[0].emit("entry", userEntry(0, "hello"));
      FakeSseStream.instances[0].emit("entry", {
        seq: 1,
        type: "assistant",
        content: [{ type: "text", text: "hi" }],
        uuid: "a-1",
      });
    });

    // Open handles reconnect through streaming clients carrying Last-Event-ID; hooks never duplicate streams.
    await act(async () => {
      await result.current.sendMessage("again");
    });
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(FakeSseStream.instances[0].close).not.toHaveBeenCalled();

    // Permanently closed handles, such as server rejection, recreate streams after last seq (after=1).
    FakeSseStream.instances[0].closed = true;
    await act(async () => {
      await result.current.sendMessage("once more");
    });
    expect(FakeSseStream.instances).toHaveLength(2);
    expect(streamOptions(1)).toMatchObject({ sessionId: "session-1", after: 1 });
  });

  it("ignores a delayed loadSession response after switching projects (no SSE leak, no state write)", async () => {
    // Project A running-session loadSession suspends at getAssistantSession; after switching to B, A's
    // late responses must create no SSE connections or write store state, which would create unconsumed
    // subscriptions for departed projects, accumulating server subscriber leaks.
    const deferredA = createDeferred<{ session: SessionMeta }>();
    vi.spyOn(API, "listAssistantSessions").mockImplementation(async (projectName) => ({
      sessions: [
        makeSession(
          projectName === "project-a" ? "session-a" : "session-b",
          projectName === "project-a" ? "running" : "idle",
        ),
      ],
    }));
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (projectName, sessionId) => {
      if (projectName === "project-a") return deferredA.promise;
      return { session: makeSession(sessionId, "idle") };
    });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(
      makeEntriesResponse({ session_id: "session-b", entries: [userEntry(0, "B-0")] }),
    );

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    // Project A loadSession suspends at getAssistantSession; deferredA remains unresolved.
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-a");
    });

    // Switch to B, whose idle session cold-reads one entry.
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
      expect(useAssistantStore.getState().turns).toHaveLength(1);
    });

    // Late A getAssistantSession returns running after cleanup abort; short-circuit
    await act(async () => {
      deferredA.resolve({ session: makeSession("session-a", "running") });
      await deferredA.promise;
    });

    // without departed-A SSE creation, retaining B state/timelines.
    expect(openStreamSpy.mock.calls.map(([options]) => options.projectName)).toEqual(["project-b"]);
    expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().turns[0].content[0].text).toBe("B-0");
  });

  it("aborts the previous loadSession on rapid session switching (no interleaved writes)", async () => {
    // Rapid session switches: next switches begin during earlier suspended cold reads, aborting prior
    // loading chains so late entries never overwrite new-session timelines.
    const deferred2 = createDeferred<EntriesResponse>();
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [
        makeSession("session-1", "idle"),
        makeSession("session-2", "idle"),
        makeSession("session-3", "idle"),
      ],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation((_projectName, sessionId) => {
      if (sessionId === "session-2") return deferred2.promise;
      return Promise.resolve(makeEntriesResponse({
        session_id: sessionId,
        entries: sessionId === "session-3" ? [userEntry(0, "S3-0")] : [],
      }));
    });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    // Switch session-2, suspending cold reads; immediately switch session-3, finishing normally.
    act(() => {
      void result.current.switchSession("session-2");
    });
    await act(async () => {
      await result.current.switchSession("session-3");
    });
    expect(useAssistantStore.getState().currentSessionId).toBe("session-3");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // Late session-2 reads now return, never overwriting session-3 timelines.
    await act(async () => {
      deferred2.resolve(makeEntriesResponse({ session_id: "session-2", entries: [userEntry(0, "S2-0")] }));
      await deferred2.promise;
    });

    expect(useAssistantStore.getState().currentSessionId).toBe("session-3");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().turns[0].content[0].text).toBe("S3-0");
  });

  it("shows an autonomous turn of the open session through its resident stream, without resubscribing", async () => {
    mockIdleSession([userEntry(0, "历史消息")]);

    renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    const stream = FakeSseStream.instances[0];

    // Project-stream recovery notices serve session lists/other views; current sessions never resubscribe because of them.
    act(() => {
      useAssistantStore.getState().notifySessionResumed("demo", "session-1");
    });
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(stream.close).not.toHaveBeenCalled();

    act(() => {
      stream.emit("status", { status: "running" });
      stream.emit("entry", { seq: 1, type: "assistant", content: [{ type: "text", text: "自主回复" }], uuid: "a-1" });
      stream.emit("question", makePendingQuestion());
    });
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    expect(useAssistantStore.getState().turns.at(-1)?.content[0].text).toBe("自主回复");
    expect(useAssistantStore.getState().pendingQuestion?.question_id).toBe("q-1");
  });

  it.each([
    ["a resume of the current session", () => useAssistantStore.getState().notifySessionResumed("demo", "session-1")],
    ["a project stream resync", () => useAssistantStore.getState().requestSessionResync("demo")],
  ])("reopens the stream on %s only when the client gave it up", async (_label, notify) => {
    mockIdleSession([userEntry(0, "历史消息")]);

    renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    // Streaming clients permanently closed handles, such as server rejection: recreate after last seq.
    FakeSseStream.instances[0].closed = true;
    act(notify);

    expect(FakeSseStream.instances).toHaveLength(2);
    expect(streamOptions(1)).toMatchObject({ sessionId: "session-1", after: 0 });
  });

  it.each([
    ["another session", "demo", "session-other"],
    ["another project", "other-project", "session-1"],
  ])("ignores resume notifications for %s", async (_label, projectName, sessionId) => {
    mockIdleSession([userEntry(0, "历史消息")]);

    renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    FakeSseStream.instances[0].closed = true;

    act(() => {
      useAssistantStore.getState().notifySessionResumed(projectName, sessionId);
    });

    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(FakeSseStream.instances).toHaveLength(1);
  });

  it("opens one stream after the cold read settles when the session resumes mid-load", async () => {
    // Loading chains already cold-read idle sessions; incoming notices create no streams before cold reads overwrite timelines,
    // leaving completion-created streams to continue.
    const deferredEntries = createDeferred<EntriesResponse>();
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "listAssistantEntries").mockReturnValue(deferredEntries.promise);

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(API.listAssistantEntries).toHaveBeenCalled();
    });

    act(() => {
      useAssistantStore.getState().notifySessionResumed("demo", "session-1");
    });
    expect(FakeSseStream.instances).toHaveLength(0);

    await act(async () => {
      deferredEntries.resolve(makeEntriesResponse({ entries: [userEntry(0, "历史消息")] }));
      await deferredEntries.promise;
    });

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });
    expect(streamOptions(0)).toMatchObject({ sessionId: "session-1", after: 0 });
  });

  it.each(["metadata", "entries"])("opens the stream when the session resumes mid-load and the %s read fails", async (phase) => {
    mockIdleSession();
    const deferredRead = createDeferred<never>();
    if (phase === "metadata") {
      vi.spyOn(API, "getAssistantSession").mockReturnValue(deferredRead.promise);
    } else {
      vi.spyOn(API, "listAssistantEntries").mockReturnValue(deferredRead.promise);
    }

    renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(phase === "metadata" ? API.getAssistantSession : API.listAssistantEntries).toHaveBeenCalled();
    });
    act(() => {
      useAssistantStore.getState().notifySessionResumed("demo", "session-1");
    });
    await act(async () => {
      deferredRead.reject(new Error("network unavailable"));
      await deferredRead.promise.catch(() => {});
    });

    expect(FakeSseStream.instances).toHaveLength(1);
    expect(streamOptions(0)).toMatchObject({ sessionId: "session-1", after: -1 });
    act(() => {
      FakeSseStream.instances[0].emit("entry", userEntry(0, "自主轮次"));
    });
    expect(useAssistantStore.getState().entries).toHaveLength(1);
  });

  it("does not let a delayed idle cold-read overwrite state set by a concurrent sendMessage", async () => {
    // While cold listAssistantEntries waits, users send within identical sessions; admitted sendMessage
    // invalidates in-flight loads. Late cold reads contain presend snapshots and must never
    // overwrite running states/appended authoritative entries.
    const deferredEntries = createDeferred<EntriesResponse>();
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "listAssistantEntries").mockReturnValue(deferredEntries.promise);
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "session-1",
      status: "accepted",
      entry: userEntry(0, "hello"),
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let accepted = false;
    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });
    expect(accepted).toBe(true);
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    expect(useAssistantStore.getState().messagesLoading).toBe(false);
    expect(FakeSseStream.instances).toHaveLength(1);

    await act(async () => {
      deferredEntries.resolve(makeEntriesResponse({ entries: [] }));
      await deferredEntries.promise;
    });

    // Retain running states/sent entries, never revert through idle snapshots captured before cold reads.
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0]);
  });

  it("no-ops switchSession when projectName is null (stale session list with no project selected)", async () => {
    // Long-lived singleton panels retain session lists after null project switches (initialization
    // effect guards); SessionHistory may therefore still render previous-project entries.
    // Clicking them must never request with null projectName.
    const getSessionSpy = vi.spyOn(API, "getAssistantSession");
    const listEntriesSpy = vi.spyOn(API, "listAssistantEntries");
    const listSessionsSpy = vi.spyOn(API, "listAssistantSessions");

    act(() => {
      useAssistantStore.getState().setSessions([makeSession("stale-session", "idle")]);
    });

    const { result } = renderHook(() => useAssistantSession(null));

    await act(async () => {
      await result.current.switchSession("stale-session");
    });

    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(listEntriesSpy).not.toHaveBeenCalled();
    expect(listSessionsSpy).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
  });

  it("ignores a delayed switchSession response after leaving the project (currentSessionId unchanged, no SSE leak)", async () => {
    // switchSession synchronously sets target currentSessionId, then users leave projects
    // (projectName → null without resetting currentSessionId). Cleanup aborts loading chains;
    // late responses short-circuit without old-closure projectName creating departed-project SSE connections.
    const deferredSwitch = createDeferred<{ session: SessionMeta }>();
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => {
      if (sessionId === "session-2") return deferredSwitch.promise;
      return { session: makeSession(sessionId, "idle") };
    });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      void result.current.switchSession("session-2");
    });
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");

    // Leave projects: projectName clears, currentSessionId remains "session-2".
    rerender({ projectName: null });

    // Late switchSession getAssistantSession now returns running.
    await act(async () => {
      deferredSwitch.resolve({ session: makeSession("session-2", "running") });
      await deferredSwitch.promise;
    });

    // Only predeparture session-1 created streams, closed on departure.
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
  });

  it("ignores a delayed init auto-selection after createNewSession", async () => {
    // Users create sessions while init listAssistantSessions waits; late responses must not
    // restore old currentSessionId or rebuild its SSE connections.
    const deferredSessions = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(deferredSessions.promise);
    const getSessionSpy = vi
      .spyOn(API, "getAssistantSession")
      .mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await act(async () => {
      result.current.createNewSession();
    });
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(useAssistantStore.getState().isDraftSession).toBe(true);
    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // Late init lists now return running sessions.
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // Project-level lists still settle; automatic selection yields to users without rebuilding SSE.
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-1"]);
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(useAssistantStore.getState().isDraftSession).toBe(true);
    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(FakeSseStream.instances).toHaveLength(0);
  });

  it("ignores a delayed init auto-selection after switchSession", async () => {
    // Same, with direct user switches during waits, such as history/deep links.
    const deferredSessions = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(deferredSessions.promise);
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await act(async () => {
      await result.current.switchSession("session-2");
    });
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");

    // Late init lists now return running sessions.
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // Never overwrite user-selected sessions or create abandoned-session SSE connections.
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-2"]);
  });

  it("ignores a delayed init auto-selection after deleteSession clears the current session", async () => {
    // Same, deleting current sessions during waits with no alternatives, clearing to sessionless.
    const deferredSessions = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(deferredSessions.promise);
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    vi.spyOn(API, "deleteAssistantSession").mockResolvedValue({ success: true });

    const { result } = renderHook(() => useAssistantSession("demo"));

    // Simulate previously selected sessions, present in stores for deletion during waits.
    act(() => {
      useAssistantStore.getState().setCurrentSessionId("session-1");
      useAssistantStore.getState().setSessions([makeSession("session-1", "idle")]);
    });

    await act(async () => {
      await result.current.deleteSession("session-1");
    });
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // Late init lists now return running sessions.
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // Never undo user deletions or create deleted-session SSE connections.
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(FakeSseStream.instances).toHaveLength(0);
  });

  it("ignores a delayed init auto-selection after sendMessage creates a session from draft", async () => {
    // During init listAssistantSessions waits, users send first messages from drafts, creating sessions;
    // late initialization must never restore old list currentSessionId.
    const deferredSessions = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(deferredSessions.promise);
    const getSessionSpy = vi
      .spyOn(API, "getAssistantSession")
      .mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "session-new",
      status: "accepted",
      entry: userEntry(0, "hello"),
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));

    let accepted = false;
    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });
    expect(accepted).toBe(true);
    expect(useAssistantStore.getState().currentSessionId).toBe("session-new");
    expect(FakeSseStream.instances).toHaveLength(1);

    // Late init session lists now return.
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // Retain newly send-created sessions without extra old-list-session SSE connections.
    expect(useAssistantStore.getState().currentSessionId).toBe("session-new");
    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(FakeSseStream.instances).toHaveLength(1);
  });

  it("keeps a slow skills response after a session operation (skills are project-level data)", async () => {
    // Skills are project-level: session operations during waits never invalidate slow skill responses,
    // or / skill commands remain unavailable until project reentry.
    const deferredSkills = createDeferred<{ skills: SkillInfo[] }>();
    vi.spyOn(API, "listAssistantSkills").mockReturnValue(deferredSkills.promise);
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    // Session operations occur during pending skill responses.
    await act(async () => {
      await result.current.switchSession("session-2");
    });

    await act(async () => {
      deferredSkills.resolve({
        skills: [{ name: "demo-skill", description: "d", scope: "project", path: "/p" }],
      });
      await deferredSkills.promise;
    });

    expect(useAssistantStore.getState().skills.map((s) => s.name)).toEqual(["demo-skill"]);
  });

  it("keeps the new project's session list when a stale session click races the init", async () => {
    // After switching to B, panels retain A session lists until B listAssistantSessions returns;
    // stale clicks invoke switchSession (silent 404), never invalidating B project-level lists,
    // whose cancellation domain is independent of session loading chains.
    const deferredB = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) => {
      if (projectName === "project-b") return deferredB.promise;
      return Promise.resolve({
        sessions: [makeSession("session-a1", "idle"), makeSession("session-a2", "idle")],
      });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (projectName, sessionId) => {
      if (projectName === "project-b" && sessionId === "session-a2") {
        throw new Error("会话不存在");
      }
      return { session: makeSession(sessionId, "idle") };
    });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-a1");
    });

    // Switch to B with pending lists; click residual A entries, silently failing 404 on B.
    rerender({ projectName: "project-b" });
    await act(async () => {
      await result.current.switchSession("session-a2");
    });

    await act(async () => {
      deferredB.resolve({ sessions: [makeSession("session-b1", "idle")] });
      await deferredB.promise;
    });

    // B lists settle for reselection without creating any B SSE; A streams closed on project switches.
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-a1"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
  });

  it("does not let a delayed session-list response overwrite the new project's list after project switch", async () => {
    // Switch A→B while A listAssistantSessions waits; A responses resolve after abort()
    // without fetch rejection, so never write A lists into B's
    // store; recheck projectAbort.signal.aborted before writes.
    const deferredA = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) => {
      if (projectName === "project-a") return deferredA.promise;
      return Promise.resolve({ sessions: [makeSession("session-b1", "idle")] });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    // Switch to B, whose lists settle normally.
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    });

    // Late A lists now return after abort; never overwrite B lists.
    await act(async () => {
      deferredA.resolve({ sessions: [makeSession("session-a1", "idle")] });
      await deferredA.promise;
    });

    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
  });

  it("does not let a delayed skills response overwrite the new project's skills after project switch", async () => {
    // Same for skills: A responses resolving after departure must not write B skills.
    const deferredA = createDeferred<{ skills: SkillInfo[] }>();
    vi.spyOn(API, "listAssistantSkills").mockImplementation((projectName) => {
      if (projectName === "project-a") return deferredA.promise;
      return Promise.resolve({
        skills: [{ name: "b-skill", description: "d", scope: "project", path: "/p" }],
      });
    });
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().skills.map((s) => s.name)).toEqual(["b-skill"]);
    });

    // Late A skills now return; never overwrite B skill lists.
    await act(async () => {
      deferredA.resolve({ skills: [{ name: "a-skill", description: "d", scope: "project", path: "/p" }] });
      await deferredA.promise;
    });

    expect(useAssistantStore.getState().skills.map((s) => s.name)).toEqual(["b-skill"]);
  });

  it("does not let a delayed turn-end session-list refresh overwrite the new project's list after project switch", async () => {
    // Project A running-session termination refreshes listAssistantSessions for SDK summary
    // titles; switching to B while waiting must prevent late A responses overwriting B lists. Terminal
    // refreshes share project cancellation domains, matching init/switchSession writes.
    const deferredRefresh = createDeferred<{ sessions: SessionMeta[] }>();
    let projectACalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) => {
      if (projectName === "project-a") {
        projectACalls += 1;
        if (projectACalls === 1) {
          return Promise.resolve({ sessions: [makeSession("session-a", "running")] });
        }
        return deferredRefresh.promise;
      }
      return Promise.resolve({ sessions: [makeSession("session-b1", "idle")] });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (projectName, sessionId) => ({
      session: makeSession(sessionId, projectName === "project-a" ? "running" : "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });

    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    // Running sessions terminate, triggering refresh suspended at deferredRefresh.
    act(() => {
      FakeSseStream.instances[0].emit("status", { status: "completed" });
    });

    // Switch to B, whose lists settle normally.
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    });

    // Late A terminal refresh returns after abort; never overwrite B lists.
    await act(async () => {
      deferredRefresh.resolve({ sessions: [makeSession("session-a-refreshed", "idle")] });
      await deferredRefresh.promise;
    });

    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
  });

  it("resets stuck loading when leaving the project while a load is in flight (no successor to take over)", async () => {
    // Leave projects (projectName null) while switchSession loadSession waits:
    // cleanup aborts loadSignal without new-project init taking over completion; effects must
    // explicitly reset messagesLoading on departure, preventing permanently true states.
    const deferredEntry = createDeferred<{ session: SessionMeta }>();
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle"), makeSession("session-2", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => {
      if (sessionId === "session-2") return deferredEntry.promise;
      return { session: makeSession(sessionId, "idle") };
    });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "demo" as string | null },
    });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.switchSession("session-2");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().messagesLoading).toBe(true);
    });

    // Leave projects; abort switchSession loading chains without successor loads.
    rerender({ projectName: null });

    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // Late getAssistantSession performs no writes after signal abort.
    await act(async () => {
      deferredEntry.resolve({ session: makeSession("session-2", "idle") });
      await deferredEntry.promise;
    });
    expect(useAssistantStore.getState().messagesLoading).toBe(false);
  });

  it("switches to the branch session after a rewrite: timeline rebuilt, stream reconnected, origin gone from the list", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, sessionId === "session-2" ? "running" : "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(
      makeEntriesResponse({ entries: [userEntry(0, "原始消息")] }),
    );
    const rewriteSpy = vi.spyOn(API, "rewriteAssistantMessage").mockResolvedValue({
      status: "accepted",
      session_id: "session-2",
      origin_session_id: "session-1",
      entry: userEntry(1, "改写后的消息"),
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().turns).toHaveLength(1);
    });
    useAssistantStore.getState().setEditingTurnUuid("u-0");

    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息");
    });

    expect(rewriteSpy).toHaveBeenCalledWith(
      "demo",
      "session-1",
      "u-0",
      "改写后的消息",
      undefined,
      expect.any(String),
    );

    const state = useAssistantStore.getState();
    expect(state.currentSessionId).toBe("session-2");
    // Old sessions disappear; new-session timeline reconstruction clears edit states.
    expect(state.sessions.map((s) => s.id)).toEqual(["session-2"]);
    expect(state.editingTurnUuid).toBeNull();
    expect(state.sending).toBe(false);
    // Switching closes old streams; running new sessions replay from start without residual timeline cursors.
    expect(FakeSseStream.instances).toHaveLength(2);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(streamOptions(1)).toMatchObject({ sessionId: "session-2", after: -1 });
    // Refresh retains new branches.
    expect(JSON.parse(localStorage.getItem("arcreel:lastSessionByProject") ?? "{}")).toEqual({ demo: "session-2" });
  });

  it("keeps the user on the origin session when a rewrite is rejected, and reuses the idempotency key on retry", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const rewriteSpy = vi
      .spyOn(API, "rewriteAssistantMessage")
      .mockRejectedValue(new Error("请先回答对话中的提问卡片，再改写消息"));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    useAssistantStore.getState().setEditingTurnUuid("u-0");

    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息");
    });

    const state = useAssistantStore.getState();
    expect(state.currentSessionId).toBe("session-1");
    expect(state.turns).toHaveLength(1);
    // Retain edit states for user correction/retry.
    expect(state.editingTurnUuid).toBe("u-0");
    expect(state.error).toBe("请先回答对话中的提问卡片，再改写消息");
    expect(state.sending).toBe(false);

    // Same-content retries reuse idempotency keys, letting servers claim already-admitted messages in new branches.
    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息");
    });
    expect(rewriteSpy.mock.calls[0][5]).toBe(rewriteSpy.mock.calls[1][5]);
  });

  it("carries the anchor's image attachments into the rewrite, empty text included", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const rewriteSpy = vi.spyOn(API, "rewriteAssistantMessage").mockResolvedValue({
      status: "accepted",
      session_id: "session-2",
      origin_session_id: "session-1",
      entry: null,
      queued_message: null,
    });
    const images = [{ data: "AAAA", media_type: "image/png" }];

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    // Image messages with emptied bodies still contain content: attachments count as content.
    await act(async () => {
      expect(await result.current.rewriteMessage("u-0", "   ", images)).toBe(true);
    });

    expect(rewriteSpy).toHaveBeenCalledWith("demo", "session-1", "u-0", "   ", images, expect.any(String));
  });

  it("rejects a rewrite with neither text nor attachments", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const rewriteSpy = vi.spyOn(API, "rewriteAssistantMessage");

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      expect(await result.current.rewriteMessage("u-0", "   ")).toBe(false);
    });

    expect(rewriteSpy).not.toHaveBeenCalled();
  });

  it("mints a new idempotency key when only the attachments differ", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const rewriteSpy = vi
      .spyOn(API, "rewriteAssistantMessage")
      .mockRejectedValue(new Error("改写失败"));

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息", [
        { data: "AAAA", media_type: "image/png" },
      ]);
    });
    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息", [
        { data: "BBBB", media_type: "image/png" },
      ]);
    });

    expect(rewriteSpy.mock.calls[0][5]).not.toBe(rewriteSpy.mock.calls[1][5]);
  });

  it("does not let a session-list refresh started before the fork resurrect the superseded origin", async () => {
    const listDeferred = createDeferred<{ sessions: SessionMeta[] }>();
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      // Initial lists return prerewrite state immediately; second, round-end refresh waits until rewrite responses.
      if (listCalls === 1) return Promise.resolve({ sessions: [makeSession("session-1", "running")] });
      return listDeferred.promise;
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(
      makeEntriesResponse({ session_id: "session-1", entries: [userEntry(0, "原始消息")] }),
    );
    vi.spyOn(API, "rewriteAssistantMessage").mockResolvedValue({
      status: "accepted",
      session_id: "session-2",
      origin_session_id: "session-1",
      entry: null,
      queued_message: null,
    });

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(1);
    });

    // Round-end refresh dispatches before server branching.
    await act(async () => {
      FakeSseStream.instances[0].emit("status", { status: "completed" });
    });
    await waitFor(() => {
      expect(listCalls).toBe(2);
    });

    await act(async () => {
      expect(await result.current.rewriteMessage("u-0", "改写后的消息")).toBe(true);
    });
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);

    // Refresh returns prebranch lists, superseded by local authoritative writes; never write back.
    await act(async () => {
      listDeferred.resolve({ sessions: [makeSession("session-1", "idle")] });
      await listDeferred.promise;
    });

    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);
  });

  it("does not let a delayed initial session-list response undo a branch created meanwhile", async () => {
    const listDeferred = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(listDeferred.promise);
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );

    const { result } = renderHook(() => useAssistantSession("demo"));

    // Initial lists remain pending while authoritative local session deletions occur.
    act(() => {
      useAssistantStore.getState().setSessions([makeSession("session-1", "idle")]);
      useAssistantStore.getState().setCurrentSessionId("session-9");
    });
    vi.spyOn(API, "deleteAssistantSession").mockResolvedValue(undefined as never);
    await act(async () => {
      await result.current.deleteSession("session-1");
    });
    expect(useAssistantStore.getState().sessions).toEqual([]);

    await act(async () => {
      listDeferred.resolve({ sessions: [makeSession("session-1", "idle")] });
      await listDeferred.promise;
    });

    // Late initial lists must not resurrect deleted sessions.
    expect(useAssistantStore.getState().sessions).toEqual([]);
  });

  it("does not auto-select from an initial session list that a deletion has already invalidated", async () => {
    // Deleting noncurrent sessions during initial loading leaves chains valid; skipping only list writes but
    // still autoselecting from these lists would load nonexistent sessions' timelines.
    const listDeferred = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(listDeferred.promise);
    const getSessionSpy = vi
      .spyOn(API, "getAssistantSession")
      .mockImplementation(async (_projectName, sessionId) => ({ session: makeSession(sessionId, "idle") }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    vi.spyOn(API, "deleteAssistantSession").mockResolvedValue(undefined as never);

    const { result } = renderHook(() => useAssistantSession("demo"));

    act(() => {
      useAssistantStore.getState().setSessions([makeSession("session-1", "idle"), makeSession("session-2", "idle")]);
    });
    // Delete noncurrent sessions; none is current yet.
    await act(async () => {
      await result.current.deleteSession("session-1");
    });
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);

    await act(async () => {
      listDeferred.resolve({ sessions: [makeSession("session-1", "idle"), makeSession("session-2", "idle")] });
      await listDeferred.promise;
    });

    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);
  });

  it("treats the session auto-selected at init as loaded, so clicking it again is a no-op", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    const entriesCalls = vi.mocked(API.listAssistantEntries).mock.calls.length;

    // Clicking already selected sessions keeps timelines and unsubmitted edit drafts intact.
    await act(async () => {
      await result.current.switchSession("session-1");
    });

    expect(vi.mocked(API.listAssistantEntries).mock.calls.length).toBe(entriesCalls);
  });

  it("skips the rewrite list reconciliation while the current session is being deleted", async () => {
    // Refreshing after deletion-invalidated rewrites may fetch branches before DELETE returns, making
    // deletion completion select them, exactly what entry invalidation must prevent.
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [userEntry(0, "原始消息")] }));
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      // Any refresh would fetch already-branched server lists.
      if (listCalls === 1) return Promise.resolve({ sessions: [makeSession("session-1", "idle")] });
      return Promise.resolve({ sessions: [makeSession("session-2", "idle")] });
    });
    const deleteDeferred = createDeferred<void>();
    vi.spyOn(API, "deleteAssistantSession").mockReturnValue(deleteDeferred.promise as never);
    const rewriteDeferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(rewriteDeferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    let deleting!: Promise<boolean>;
    await act(async () => {
      deleting = result.current.deleteSession("session-1");
      rewriteDeferred.resolve({
        status: "accepted",
        session_id: "session-2",
        origin_session_id: "session-1",
        entry: null,
        queued_message: null,
      });
      await rewriteDeferred.promise;
    });

    // No refresh during DELETE; returned branches would be selected by deletion completion.
    expect(listCalls).toBe(1);

    await act(async () => {
      deleteDeferred.resolve();
      await deleting;
    });

    // Deletion completion never moves users into branches; accounting refreshes afterward keep branches visible in lists.
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);
    });
  });

  it("keeps the rewrite reconciliation suppressed while another session's deletion finishes first", async () => {
    // Delete other listed sessions while current deletion waits; earlier completion must not remove current-session protection.
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [userEntry(0, "原始消息")] }));
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      if (listCalls === 1) {
        return Promise.resolve({ sessions: [makeSession("session-1", "idle"), makeSession("session-3", "idle")] });
      }
      return Promise.resolve({ sessions: [makeSession("session-2", "idle")] });
    });
    const deleteCurrentDeferred = createDeferred<void>();
    vi.spyOn(API, "deleteAssistantSession").mockImplementation((_projectName, sessionId) => {
      if (sessionId === "session-1") return deleteCurrentDeferred.promise as never;
      return Promise.resolve(undefined as never);
    });
    const rewriteDeferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(rewriteDeferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    let deletingCurrent!: Promise<boolean>;
    await act(async () => {
      deletingCurrent = result.current.deleteSession("session-1");
      // Other-session deletion finishes fully without affecting current-session protection.
      await result.current.deleteSession("session-3");
      rewriteDeferred.resolve({
        status: "accepted",
        session_id: "session-2",
        origin_session_id: "session-1",
        entry: null,
        queued_message: null,
      });
      await rewriteDeferred.promise;
    });

    // Current DELETE still waits; protections remain and prevent refresh.
    expect(listCalls).toBe(1);

    await act(async () => {
      deleteCurrentDeferred.resolve();
      await deletingCurrent;
    });
    expect(useAssistantStore.getState().currentSessionId).not.toBe("session-2");
  });

  it("keeps a pending deletion of one session from suppressing another session's rewrite reconciliation", async () => {
    // Rewrite session-2 while same-project session-1 deletion waits; protect only deleted source sessions,
    // or session-2 branches never enter lists/create SSE and remain unreachable until page refresh.
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      return Promise.resolve({ sessions: [makeSession("session-1", "idle"), makeSession("session-2", "idle")] });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );
    // session-1 deletion remains pending.
    vi.spyOn(API, "deleteAssistantSession").mockReturnValue(createDeferred<void>().promise as never);
    const rewriteDeferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(rewriteDeferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    act(() => {
      void result.current.deleteSession("session-1");
    });

    await act(async () => {
      await result.current.switchSession("session-2");
    });
    const callsBefore = listCalls;

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await act(async () => {
      await result.current.switchSession("session-1");
      rewriteDeferred.resolve({
        status: "accepted",
        session_id: "session-3",
        origin_session_id: "session-2",
        entry: null,
        queued_message: null,
      });
      await rewriteDeferred.promise;
    });

    await waitFor(() => {
      expect(listCalls).toBeGreaterThan(callsBefore);
    });
  });

  it("keeps a pending deletion in one project from suppressing another project's rewrite reconciliation", async () => {
    const listCalls: Record<string, number> = { "project-a": 0, "project-b": 0 };
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) => {
      listCalls[projectName] = (listCalls[projectName] ?? 0) + 1;
      if (projectName === "project-a") return Promise.resolve({ sessions: [makeSession("a-s1", "idle")] });
      return Promise.resolve({ sessions: [makeSession("b-s1", "idle"), makeSession("b-s2", "idle")] });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );
    // Project A deletion remains pending, simulating slow requests.
    vi.spyOn(API, "deleteAssistantSession").mockReturnValue(createDeferred<void>().promise as never);
    const rewriteDeferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(rewriteDeferred.promise);

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("a-s1");
    });
    act(() => {
      void result.current.deleteSession("a-s1");
    });

    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("b-s1");
    });
    const callsBefore = listCalls["project-b"];

    // B rewrites invalidate on session switches; A's pending deletion must not suppress B refreshes.
    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await act(async () => {
      await result.current.switchSession("b-s2");
      rewriteDeferred.resolve({
        status: "accepted",
        session_id: "b-s3",
        origin_session_id: "b-s1",
        entry: null,
        queued_message: null,
      });
      await rewriteDeferred.promise;
    });

    await waitFor(() => {
      expect(listCalls["project-b"]).toBeGreaterThan(callsBefore);
    });
  });

  it("does not compensate a failed deletion into the project switched to meanwhile", async () => {
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) =>
      Promise.resolve({ sessions: [makeSession(`${projectName}-s1`, "idle")] }),
    );
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );
    const deleteDeferred = createDeferred<void>();
    vi.spyOn(API, "deleteAssistantSession").mockReturnValue(deleteDeferred.promise as never);

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("project-a-s1");
    });

    let deleting!: Promise<boolean>;
    act(() => {
      deleting = result.current.deleteSession("project-a-s1");
    });

    // Switch to B during DELETE.
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("project-b-s1");
    });
    const entriesCalls = vi.mocked(API.listAssistantEntries).mock.calls.length;

    // Old-project DELETE now fails; compensation must not use old projectName to load B current sessions.
    await act(async () => {
      deleteDeferred.reject(new Error("delete failed"));
      await deleting;
    });

    expect(useAssistantStore.getState().currentSessionId).toBe("project-b-s1");
    expect(vi.mocked(API.listAssistantEntries).mock.calls.length).toBe(entriesCalls);
  });

  it("does not fold the previous project's sessions into the list when creating one during initialization", async () => {
    // Create/send while B lists wait after switching; merging residual A sessions from stores would
    // obsolete/discard B authoritative lists and leave foreign sessions permanently visible.
    const listB = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockImplementation((projectName) => {
      if (projectName === "project-a") {
        return Promise.resolve({ sessions: [{ ...makeSession("a-s1", "idle"), project_name: "project-a" }] });
      }
      return listB.promise;
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({
      session_id: "b-new",
      status: "accepted",
      entry: userEntry(0, "hello"),
      queued_message: null,
    });

    const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
      initialProps: { projectName: "project-a" as string | null },
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["a-s1"]);
    });

    rerender({ projectName: "project-b" });
    act(() => {
      result.current.createNewSession();
    });
    await act(async () => {
      expect(await result.current.sendMessage("hello")).toBe(true);
    });

    // Retain new current-project sessions only; never mix A sessions.
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["b-new"]);
  });

  it("keeps the idempotency key and reconciles the list when a stale rewrite fails in transit", async () => {
    // Network interruption leaves admission uncertain: fresh-key retries truly branch twice, while unreconciled admitted branches
    // appear only after page refresh.
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      return Promise.resolve({ sessions: [makeSession("session-1", "idle"), makeSession("session-2", "idle")] });
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );
    const rewriteDeferred = createDeferred<never>();
    const rewriteSpy = vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(rewriteDeferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    const callsBefore = listCalls;

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    // Switching invalidates this round, then rewrites fail with network errors.
    await act(async () => {
      await result.current.switchSession("session-2");
      rewriteDeferred.reject(new Error("network down"));
      await rewriteDeferred.promise.catch(() => {});
    });

    await waitFor(() => {
      expect(listCalls).toBeGreaterThan(callsBefore);
    });

    // Return to original sessions and retry with identical idempotency keys, claiming authoritative entries rather than branching again.
    await act(async () => {
      await result.current.switchSession("session-1");
    });
    const firstKey = rewriteSpy.mock.calls[0][5];
    rewriteSpy.mockResolvedValue({
      status: "accepted",
      session_id: "session-3",
      origin_session_id: "session-1",
      entry: null,
      queued_message: null,
    });
    await act(async () => {
      await result.current.rewriteMessage("u-0", "改写后的消息");
    });
    expect(rewriteSpy.mock.calls[1][5]).toBe(firstKey);
  });

  it("surfaces a failed session load and lets the same session be retried", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle"), makeSession("session-2", "idle")],
    });
    let failedOnce = false;
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => {
      if (sessionId === "session-2" && !failedOnce) {
        failedOnce = true;
        throw new Error("network down");
      }
      return { session: makeSession(sessionId, "idle") };
    });
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.switchSession("session-2");
    });
    expect(useAssistantStore.getState().error).toBe("会话内容加载失败，请重新选择该会话重试");
    expect(useAssistantStore.getState().turns).toEqual([]);

    // Clicking identical sessions no longer short-circuits; successful retries clear failure notices.
    await act(async () => {
      await result.current.switchSession("session-2");
    });
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().error).toBeNull();
  });

  it.each(["project", "new-session", "delete-last"] as const)(
    "drops the previous session's error after navigating away (%s)",
    async (navigation) => {
      vi.spyOn(API, "listAssistantSessions").mockResolvedValue({ sessions: [makeSession("session-1", "running")] });
      vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
      vi.spyOn(API, "interruptAssistantSession").mockRejectedValue(new Error("中断请求失败"));
      vi.spyOn(API, "deleteAssistantSession").mockResolvedValue({ success: true });
      const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
        initialProps: { projectName: "demo" },
      });
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));

      await act(async () => { await result.current.interrupt(); });
      // Errors display normally within identical sessions.
      expect(useAssistantStore.getState().error).toBe("中断请求失败");

      if (navigation === "project") {
        rerender({ projectName: "other" });
      } else if (navigation === "new-session") {
        act(() => { result.current.createNewSession(); });
      } else {
        await act(async () => { await result.current.deleteSession("session-1"); });
      }
      // After project switches, new sessions finish loading/attach streams; creation/deleting all leaves sessionless states.
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(navigation === "project" ? 2 : 1));
      expect(useAssistantStore.getState().currentSessionId).toBe(navigation === "project" ? "session-1" : null);
      expect(useAssistantStore.getState().error).toBeNull();
    },
  );

  it.each(["project", "new-session"] as const)(
    "ignores the old session's interrupt failure after switching %s",
    async (navigation) => {
      vi.spyOn(API, "listAssistantSessions").mockResolvedValue({ sessions: [makeSession("session-1", "running")] });
      vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
      const interrupted = createDeferred<Awaited<ReturnType<typeof API.interruptAssistantSession>>>();
      vi.spyOn(API, "interruptAssistantSession").mockReturnValue(interrupted.promise);
      const { result, rerender } = renderHook(({ projectName }) => useAssistantSession(projectName), {
        initialProps: { projectName: "demo" },
      });
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));
      let request: Promise<void>;
      act(() => { request = result.current.interrupt(); });

      if (navigation === "project") {
        rerender({ projectName: "other" });
      } else {
        act(() => { result.current.createNewSession(); });
      }
      await waitFor(() => expect(FakeSseStream.instances).toHaveLength(navigation === "project" ? 2 : 1));
      expect(useAssistantStore.getState().interrupting).toBe(false);
      await act(async () => {
        interrupted.reject(new Error("old interrupt failed"));
        await request!;
      });
      expect(useAssistantStore.getState().error).toBeNull();
    },
  );

  it("invalidates a pending rewrite as soon as the current session is deleted", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    // Suspend DELETE so rewrite admission occurs during its in-flight window.
    const deleteDeferred = createDeferred<void>();
    vi.spyOn(API, "deleteAssistantSession").mockReturnValue(deleteDeferred.promise as never);
    const deferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    // Current-session deletion races rewrite admission: invalidation starts at DELETE dispatch, never waits for responses.
    await act(async () => {
      const deleting = result.current.deleteSession("session-1");
      deferred.resolve({
        status: "accepted",
        session_id: "session-2",
        origin_session_id: "session-1",
        entry: null,
        queued_message: null,
      });
      await deferred.promise;
      deleteDeferred.resolve();
      await deleting;
    });

    // Late admission must not move users into branches.
    expect(useAssistantStore.getState().currentSessionId).not.toBe("session-2");
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).not.toContain("session-2");
  });

  it("tells the caller whether the session was deleted", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const remove = vi.spyOn(API, "deleteAssistantSession").mockRejectedValueOnce(new Error("delete failed"));

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    let deleted: boolean | undefined;
    await act(async () => {
      deleted = await result.current.deleteSession("session-1");
    });
    expect(deleted).toBe(false);

    remove.mockResolvedValueOnce({ success: true });
    await act(async () => {
      deleted = await result.current.deleteSession("session-1");
    });
    expect(deleted).toBe(true);
    expect(useAssistantStore.getState().sessions).toEqual([]);
  });

  it("reloads the current session when deletion fails, so a send accepted meanwhile is not orphaned", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    vi.spyOn(API, "deleteAssistantSession").mockRejectedValue(new Error("delete failed"));

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    const entriesCallsBefore = vi.mocked(API.listAssistantEntries).mock.calls.length;

    await act(async () => {
      await result.current.deleteSession("session-1");
    });

    // Failed deletion leaves sessions: reload once so server-admitted rounds do not remain locally invisible.
    expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    expect(vi.mocked(API.listAssistantEntries).mock.calls.length).toBeGreaterThan(entriesCallsBefore);
  });

  it("marks a rewrite startup failure by origin so the card does not replay the composer", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    const failure = {
      version: 1,
      phase: "startup" as const,
      timestamp: "2026-07-23T01:02:03Z",
      project_name: "demo",
      session_id: "session-1",
      summary: {
        source: "local_exception",
        type: "ProcessError",
        message: "Claude Code exited before initialization",
      },
      raw: {},
    };
    vi.spyOn(API, "rewriteAssistantMessage").mockRejectedValue(
      new AgentFailureError("Agent 启动失败", failure),
    );

    const { result } = renderHook(() => useAssistantSession("demo"));
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });
    useAssistantStore.getState().setEditingTurnUuid("u-0");

    await act(async () => {
      expect(await result.current.rewriteMessage("u-0", "改写后的消息")).toBe(false);
    });

    const state = useAssistantStore.getState();
    expect(state.startupFailure).toEqual(failure);
    expect(state.startupFailureOrigin).toBe("rewrite");
    // Original inputs remain in open editors, which own replay.
    expect(state.editingTurnUuid).toBe("u-0");
  });

  it("ignores a delayed rewrite completion after switching sessions (no yank to the branch)", async () => {
    const listSessions = vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle"), makeSession("session-3", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockImplementation(async (_projectName, sessionId) =>
      makeEntriesResponse({ session_id: sessionId, entries: [userEntry(0, sessionId)] }),
    );
    const deferred = createDeferred<AcceptedMessageResponse & { origin_session_id: string | null }>();
    vi.spyOn(API, "rewriteAssistantMessage").mockReturnValue(deferred.promise);

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      void result.current.rewriteMessage("u-0", "改写后的消息");
    });
    await waitFor(() => {
      expect(useAssistantStore.getState().sending).toBe(true);
    });

    // Switching before rewrite returns invalidates rounds; switches reset sending.
    await act(async () => {
      await result.current.switchSession("session-3");
    });
    expect(useAssistantStore.getState().sending).toBe(false);

    // Servers already replaced session-1 with new branch session-2.
    listSessions.mockResolvedValue({
      sessions: [makeSession("session-2", "running"), makeSession("session-3", "idle")],
    });

    await act(async () => {
      deferred.resolve({
        status: "accepted",
        session_id: "session-2",
        origin_session_id: "session-1",
        entry: userEntry(1, "改写后的消息"),
        queued_message: null,
      });
      await deferred.promise;
    });

    // Late admission never pulls users from selected sessions into branches.
    expect(useAssistantStore.getState().currentSessionId).toBe("session-3");
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1", "session-3"]);
    // Server branching already happened, so refresh lists once, replacing vanished session-1 with session-2.
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2", "session-3"]);
    });
  });
});
