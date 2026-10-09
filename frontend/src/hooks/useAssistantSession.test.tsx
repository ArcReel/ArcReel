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

  /** 第 index 次建流时传给 `API.openAssistantEntriesStream` 的参数（会话与冷订阅游标）。 */
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
    // 冷读之后接上常驻 entry 流，游标从冷读的最后一条之后开始
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
      // 重复 seq：身份（seq）门槛去重，不做内容比对
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

    // Agent 接纳第一条：条目先到，随后移出托盘
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
      // 流上的移出可能晚于响应到达：内容不再退回第二次
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
      // Agent 最终没有处理它：按编辑退回输入框
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

    // 第二条的响应迟到：流上它已被接纳、离开排队
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
      // 重连首帧快照：携带累积态 + rev 门槛
      FakeSseStream.instances[0].emit("draft", {
        session_id: "session-1",
        draft: { message_id: "msg_1", content: [{ type: "text", text: "部分" }], rev: 5 },
        rev: 5,
      });
      // 订阅间隙重复投递的 delta（rev ≤ 门槛）须被过滤
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
      // 同 message_id 的权威条目落库：draft 被精确替换（身份比对）
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

    // 中断保留 draft（被中断内容不入日志，刷新后自然消失）
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

    // 开场与上一轮收尾下发的终态 status
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
    // CLI 没接纳就退出：消息离开排队、没有条目，会话落到终态
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
    // 无本地合成消息：时间线唯一来源是响应携带的权威条目
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0]);
    expect(useAssistantStore.getState().turns).toEqual([
      { type: "user", content: [{ type: "text", text: "hello" }], uuid: "u-0", timestamp: undefined },
    ]);
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    // 发送复用打开会话时建立的那条流
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(streamOptions(0)).toMatchObject({ projectName: "demo", sessionId: "session-1" });
    expect(FakeSseStream.instances[0].close).not.toHaveBeenCalled();
    // client_key 随请求发送
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

    // 失败：无合成消息可回滚，时间线不变、状态不变、输入由调用方保留
    expect(accepted).toBe(false);
    expect(useAssistantStore.getState().sending).toBe(false);
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(useAssistantStore.getState().turns).toEqual([]);
    expect(useAssistantStore.getState().error).toBe("发送失败");

    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });

    expect(accepted).toBe(true);
    // 同内容重试复用同一幂等键：服务端按键去重，不产生重复
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
    // 面板为长生命周期单例，切换项目不卸载：项目 A 的失败缓存（clientKey + 签名）
    // 会跨项目存活。签名含项目维度后，切到 B 重发同内容应生成新键、不复用 A 的键。
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败")) // 项目 A：失败并缓存 clientKey
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

    // 切换项目（同一 hook 实例，失败缓存的 ref 存活）
    rerender({ p: "proj_b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(sendSpy).toHaveBeenCalledTimes(2);
    // 跨项目同内容重发：签名含项目维度 → 生成新键，不复用旧项目缓存的 clientKey
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

    // 迟到的发送完成不得污染已切换会话的时间线，也不为原会话建流
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1", "session-2"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
  });

  it("resolves false for a send invalidated by switching sessions mid-flight", async () => {
    // 版本失配分支必须与失败路径一致返回 false：调用方（AgentCopilot.handleSend）
    // 依据返回值清空输入框，误返回 true 会清空用户已切换到的新会话里刚输入的内容。
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
    // 独立于版本号的第二道防线：currentSessionId 是跨 hook 实例共享的全局 store 字段，
    // 可能被本实例之外的路径直接改写（不经过 invalidatePendingSend）。即便版本号未失配，
    // 只要响应回来时 currentSessionId 已不是发送时的会话，也不能返回 true 清空新会话输入框。
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

    // 项目 A：running 会话冷订阅建流，灌入残留条目
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

    // 切到项目 B（其会话亦为 running）
    rerender({ projectName: "project-b" });

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
    });
    await waitFor(() => {
      expect(FakeSseStream.instances).toHaveLength(2);
    });

    // 冷订阅游标由重置后的空 entries 推导（after=-1，等效从头订阅），
    // 不被 A 的残留最大 seq 污染
    expect(streamOptions(1).after).toBe(-1);
    // 时间线不含 A 的条目
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

    // 项目 A：idle 会话冷读出一条历史条目
    await waitFor(() => {
      expect(useAssistantStore.getState().turns).toHaveLength(1);
    });

    // 切到无会话项目：初始化路径「无会话」分支同样重置时间线
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
    // 首帧冷订阅从头开始（entries 为空）
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

    // 句柄仍打开：断线重建由流式客户端带 Last-Event-ID 完成，hook 不重复建流
    await act(async () => {
      await result.current.sendMessage("again");
    });
    expect(FakeSseStream.instances).toHaveLength(1);
    expect(FakeSseStream.instances[0].close).not.toHaveBeenCalled();

    // 句柄已被客户端永久关闭（如服务端拒绝）：再次建流时游标停在最后 seq（after=1）
    FakeSseStream.instances[0].closed = true;
    await act(async () => {
      await result.current.sendMessage("once more");
    });
    expect(FakeSseStream.instances).toHaveLength(2);
    expect(streamOptions(1)).toMatchObject({ sessionId: "session-1", after: 1 });
  });

  it("ignores a delayed loadSession response after switching projects (no SSE leak, no state write)", async () => {
    // 项目 A 的 running 会话：loadSession 卡在 getAssistantSession；切到 B 后 A 的
    // 迟到响应不得建 SSE 连接、不得回写任何 store 状态（否则为已离开的项目建立
    // 无人消费的 SSE 订阅，服务端订阅者堆积泄漏）。
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

    // 项目 A：loadSession 卡在 getAssistantSession（deferredA 未 resolve）
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-a");
    });

    // 切到项目 B（其 idle 会话冷读一条条目）
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
      expect(useAssistantStore.getState().turns).toHaveLength(1);
    });

    // 迟到：A 的 getAssistantSession 此刻才返回 running；signal 已被 cleanup abort，短路
    await act(async () => {
      deferredA.resolve({ session: makeSession("session-a", "running") });
      await deferredA.promise;
    });

    // 不为已离开的项目 A 建 SSE 连接；状态与时间线保持项目 B
    expect(openStreamSpy.mock.calls.map(([options]) => options.projectName)).toEqual(["project-b"]);
    expect(useAssistantStore.getState().currentSessionId).toBe("session-b");
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().turns[0].content[0].text).toBe("B-0");
  });

  it("aborts the previous loadSession on rapid session switching (no interleaved writes)", async () => {
    // 快速连续切会话：前一次切换的冷读挂起期间发起下一次切换，前任加载链被
    // abort，迟到的 entries 不得覆盖新会话时间线。
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

    // 切到 session-2：冷读挂起；随即切到 session-3：正常完成
    act(() => {
      void result.current.switchSession("session-2");
    });
    await act(async () => {
      await result.current.switchSession("session-3");
    });
    expect(useAssistantStore.getState().currentSessionId).toBe("session-3");
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // 迟到：session-2 的冷读此刻才返回，不得覆盖 session-3 的时间线
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

    // 项目事件流的恢复通知留给会话列表等视图，当前会话不因它重新订阅
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

    // 句柄已被流式客户端永久关闭（如服务端拒绝）：补建，游标停在最后 seq
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
    // 加载链已按 idle 走冷读：此时到达的通知不建流（冷读随后整帧覆写时间线），
    // 由加载收尾时建立的那条流接上。
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
    // 冷读 listAssistantEntries 挂起期间，用户在同一会话内发送消息：sendMessage
    // 受理后作废在途加载链。冷读迟到完成后携带的是发消息前的旧快照，不得据此
    // 覆盖 running 状态与已追加的权威条目。
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

    // running 状态与已发送的条目保留，不被冷读前捕获的旧 idle 快照回退
    expect(useAssistantStore.getState().sessionStatus).toBe("running");
    expect(useAssistantStore.getState().entries.map((e) => e.seq)).toEqual([0]);
  });

  it("no-ops switchSession when projectName is null (stale session list with no project selected)", async () => {
    // 面板为长生命周期单例，切项目为 null 后 sessions 列表不清空（见初始化
    // effect 的前置 guard）；SessionHistory 据此仍可能渲染出旧项目的会话项。
    // 点击它们不得以 null projectName 发起请求。
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
    // switchSession 同步把 currentSessionId 设为目标 sessionId，随后用户离开项目
    // （projectName -> null，不重置 currentSessionId）。effect cleanup abort 加载链，
    // 迟到响应短路，不用旧闭包的 projectName 为已离开的项目建 SSE 连接。
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

    // 离开项目：projectName 置空，currentSessionId 不受影响，仍是 "session-2"
    rerender({ projectName: null });

    // 迟到：switchSession 发起的 getAssistantSession 此刻才返回 running
    await act(async () => {
      deferredSwitch.resolve({ session: makeSession("session-2", "running") });
      await deferredSwitch.promise;
    });

    // 只有离开前打开的 session-1 建过流，且已随离开项目关闭
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
  });

  it("ignores a delayed init auto-selection after createNewSession", async () => {
    // init 的 listAssistantSessions 挂起期间用户新建会话：迟到响应不得把
    // currentSessionId 覆盖回旧会话并为其重建 SSE 连接。
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

    // 迟到：init 的会话列表此刻才返回，携带一个 running 会话
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // 列表（项目级数据）照常落地；自动选择让位于用户操作，不重建 SSE
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-1"]);
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(useAssistantStore.getState().isDraftSession).toBe(true);
    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(FakeSseStream.instances).toHaveLength(0);
  });

  it("ignores a delayed init auto-selection after switchSession", async () => {
    // 同上，换成挂起期间用户直接切到另一会话（如通过历史记录/深链接跳转）。
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

    // 迟到：init 的会话列表此刻才返回，携带一个 running 会话
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // 不覆盖用户已切到的会话，不为已放弃的会话建 SSE 连接
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-2"]);
  });

  it("ignores a delayed init auto-selection after deleteSession clears the current session", async () => {
    // 同上，换成挂起期间用户删除当前会话且无其它会话可切（清空到无会话）。
    const deferredSessions = createDeferred<{ sessions: SessionMeta[] }>();
    vi.spyOn(API, "listAssistantSessions").mockReturnValue(deferredSessions.promise);
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [] }));
    vi.spyOn(API, "deleteAssistantSession").mockResolvedValue({ success: true });

    const { result } = renderHook(() => useAssistantSession("demo"));

    // 模拟用户此前已选中会话（挂起期间 store 中已有当前会话可删）
    act(() => {
      useAssistantStore.getState().setCurrentSessionId("session-1");
      useAssistantStore.getState().setSessions([makeSession("session-1", "idle")]);
    });

    await act(async () => {
      await result.current.deleteSession("session-1");
    });
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // 迟到：init 的会话列表此刻才返回，携带一个 running 会话
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // 不覆盖用户的删除操作，不为已删除的会话建 SSE 连接
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    expect(FakeSseStream.instances).toHaveLength(0);
  });

  it("ignores a delayed init auto-selection after sendMessage creates a session from draft", async () => {
    // init 的 listAssistantSessions 挂起期间用户从草稿直接发送首条消息建会话：
    // 迟到的初始化响应不得把 currentSessionId 覆盖回列表里的旧会话。
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

    // 迟到：init 的会话列表此刻才返回
    await act(async () => {
      deferredSessions.resolve({ sessions: [makeSession("session-1", "running")] });
      await deferredSessions.promise;
    });

    // 不覆盖发送建立的新会话，不为列表里的旧会话另建 SSE 连接
    expect(useAssistantStore.getState().currentSessionId).toBe("session-new");
    expect(getSessionSpy).not.toHaveBeenCalled();
    expect(FakeSseStream.instances).toHaveLength(1);
  });

  it("keeps a slow skills response after a session operation (skills are project-level data)", async () => {
    // 技能列表是项目级数据：挂起期间的会话操作不作废它，慢响应照常落地，
    // 否则 / 技能命令在该项目内持续不可用直到重进项目。
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

    // 技能响应挂起期间发生会话操作
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
    // 项目切换后 B 的 listAssistantSessions 未返回前，面板仍渲染 A 的残留会话
    // 列表；点击陈旧项触发 switchSession（请求以 404 静默失败），不得作废 B 的
    // 会话列表落地——列表是项目级数据，走独立于会话加载链的取消域。
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

    // 切到项目 B：其会话列表挂起；点击残留的 A 会话项（在 B 上 404 静默失败）
    rerender({ projectName: "project-b" });
    await act(async () => {
      await result.current.switchSession("session-a2");
    });

    await act(async () => {
      deferredB.resolve({ sessions: [makeSession("session-b1", "idle")] });
      await deferredB.promise;
    });

    // B 的会话列表照常落地，供用户重新选择；不为 B 的任何会话建 SSE 连接，A 的流已随切项目关闭
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-a1"]);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
  });

  it("does not let a delayed session-list response overwrite the new project's list after project switch", async () => {
    // 项目 A 的 listAssistantSessions 挂起期间切到项目 B；A 的响应在 abort() 之后
    // 才 resolve（fetch 不会因此 reject），此时不得把 A 的会话列表写入已切到 B 的
    // store（写入前须复核 projectAbort.signal.aborted）。
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

    // 切到项目 B：其会话列表正常落地
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    });

    // 迟到：A 的会话列表此刻才返回，signal 已 abort，不得覆盖 B 的列表
    await act(async () => {
      deferredA.resolve({ sessions: [makeSession("session-a1", "idle")] });
      await deferredA.promise;
    });

    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
  });

  it("does not let a delayed skills response overwrite the new project's skills after project switch", async () => {
    // 同上，针对技能列表：A 的响应在离开 A 之后才 resolve，不得写入 B 的 skills。
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

    // 迟到：A 的技能列表此刻才返回，不得覆盖 B 的技能列表
    await act(async () => {
      deferredA.resolve({ skills: [{ name: "a-skill", description: "d", scope: "project", path: "/p" }] });
      await deferredA.promise;
    });

    expect(useAssistantStore.getState().skills.map((s) => s.name)).toEqual(["b-skill"]);
  });

  it("does not let a delayed turn-end session-list refresh overwrite the new project's list after project switch", async () => {
    // 项目 A 的 running 会话终态触发 listAssistantSessions 刷新（获取 SDK summary
    // 标题）；响应挂起期间切到项目 B，A 的迟到响应不得覆盖 B 的会话列表——终态
    // 刷新纳入项目级取消域，与 init/switchSession 的写入同口径。
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

    // running 会话终态：触发刷新，挂起在 deferredRefresh
    act(() => {
      FakeSseStream.instances[0].emit("status", { status: "completed" });
    });

    // 切到项目 B：其会话列表正常落地
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
    });

    // 迟到：A 的终态刷新此刻才返回，signal 已 abort，不得覆盖 B 的列表
    await act(async () => {
      deferredRefresh.resolve({ sessions: [makeSession("session-a-refreshed", "idle")] });
      await deferredRefresh.promise;
    });

    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-b1"]);
  });

  it("resets stuck loading when leaving the project while a load is in flight (no successor to take over)", async () => {
    // switchSession 的 loadSession 挂起期间离开项目（projectName 变为 null）：
    // cleanup abort 了 loadSignal，但没有新项目的 init 接管收尾——effect 需在
    // 离开分支显式复位 messagesLoading，否则永久卡在 true。
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

    // 离开项目：switchSession 的加载链被 abort，但没有后续加载链接管
    rerender({ projectName: null });

    expect(useAssistantStore.getState().messagesLoading).toBe(false);

    // 迟到的 getAssistantSession 不再产生任何写入（signal 已 abort）
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
    // 旧会话不再出现在列表；改写后的时间线由新会话整体重建，编辑态随之清空
    expect(state.sessions.map((s) => s.id)).toEqual(["session-2"]);
    expect(state.editingTurnUuid).toBeNull();
    expect(state.sending).toBe(false);
    // 原会话的流随切换关闭；running 的新会话由 entry 流从头回放，游标不带上一条时间线的残留
    expect(FakeSseStream.instances).toHaveLength(2);
    expect(FakeSseStream.instances[0].close).toHaveBeenCalled();
    expect(streamOptions(1)).toMatchObject({ sessionId: "session-2", after: -1 });
    // 刷新后仍停在新分支
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
    // 编辑态保留，用户可以改完再试
    expect(state.editingTurnUuid).toBe("u-0");
    expect(state.error).toBe("请先回答对话中的提问卡片，再改写消息");
    expect(state.sending).toBe(false);

    // 同内容重试复用同一幂等键：服务端据此在新分支里认领已受理的那一条
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

    // 正文被改空的带图消息仍是有内容的消息，附件即内容
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
      // 首次（init）立即给出改写前的列表；第二次（轮次结束的补拉）挂起到改写返回之后
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

    // 轮次结束触发补拉，该请求在服务端分叉之前就已发出
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

    // 补拉这才返回，带回的是分叉前的列表——已被本地权威写入取代，不得写回
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

    // 初始列表还在途，此间已有本地权威写入（删除会话）
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

    // 迟到的初始列表不得让已删除的会话复活
    expect(useAssistantStore.getState().sessions).toEqual([]);
  });

  it("does not auto-select from an initial session list that a deletion has already invalidated", async () => {
    // 初始列表在途期间删除的是「非当前」会话：加载链不会被作废，若只跳过写列表、
    // 仍照这份名单自动选择，就会选中一条已经不存在的会话并去加载它的时间线。
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
    // 删的不是当前会话（此刻还没有当前会话）
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

    // 点一下本来就选中的这条：不重建时间线，也就不会丢掉还没提交的编辑草稿
    await act(async () => {
      await result.current.switchSession("session-1");
    });

    expect(vi.mocked(API.listAssistantEntries).mock.calls.length).toBe(entriesCalls);
  });

  it("skips the rewrite list reconciliation while the current session is being deleted", async () => {
    // 改写被删除作废后照常补拉列表的话，补拉可能赶在 DELETE 返回前把新分支带进
    // 列表，删除收尾读到它就会顺手切过去——正是进入时那次作废要避免的结果。
    vi.spyOn(API, "getAssistantSession").mockImplementation(async (_projectName, sessionId) => ({
      session: makeSession(sessionId, "idle"),
    }));
    vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries: [userEntry(0, "原始消息")] }));
    let listCalls = 0;
    vi.spyOn(API, "listAssistantSessions").mockImplementation(() => {
      listCalls += 1;
      // 补拉一旦发生，拿到的就是服务端已分叉后的列表
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

    // DELETE 在途期间不补拉：拉回来的分支会被删除收尾顺手选中
    expect(listCalls).toBe(1);

    await act(async () => {
      deleteDeferred.resolve();
      await deleting;
    });

    // 删除收尾没把用户装到分支上，但记账的补拉在收尾后补上，分支不至于消失在列表外
    expect(useAssistantStore.getState().currentSessionId).toBeNull();
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2"]);
    });
  });

  it("keeps the rewrite reconciliation suppressed while another session's deletion finishes first", async () => {
    // 删除当前会话在途期间删掉列表里的另一条：后者先收尾，不得替前者把保护摘掉。
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
      // 另一条会话的删除整轮走完，其收尾不涉及当前会话的保护
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

    // 当前会话的 DELETE 还在途，保护仍在：补拉不得发生
    expect(listCalls).toBe(1);

    await act(async () => {
      deleteCurrentDeferred.resolve();
      await deletingCurrent;
    });
    expect(useAssistantStore.getState().currentSessionId).not.toBe("session-2");
  });

  it("keeps a pending deletion of one session from suppressing another session's rewrite reconciliation", async () => {
    // 同项目内删 session-1 挂起期间改写 session-2：保护只针对被删的那条源会话，
    // 否则 session-2 的分支既不进列表也不建 SSE，刷新页面前一直找不回来。
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
    // session-1 的删除一直挂着
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
    // 项目 A 的删除一直挂着，模拟慢请求
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

    // B 里的改写被切会话作废：A 那次仍在途的删除不该连带压掉 B 的补拉
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

    // DELETE 在途期间切到项目 B
    rerender({ projectName: "project-b" });
    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("project-b-s1");
    });
    const entriesCalls = vi.mocked(API.listAssistantEntries).mock.calls.length;

    // 旧项目的 DELETE 这才失败：补偿不得拿旧 projectName 去加载 B 的当前会话
    await act(async () => {
      deleteDeferred.reject(new Error("delete failed"));
      await deleting;
    });

    expect(useAssistantStore.getState().currentSessionId).toBe("project-b-s1");
    expect(vi.mocked(API.listAssistantEntries).mock.calls.length).toBe(entriesCalls);
  });

  it("does not fold the previous project's sessions into the list when creating one during initialization", async () => {
    // 切到 B 后 B 的会话列表还在途，此刻新建并发送：合并时若把仍留在 store 里的 A
    // 会话一起写进去，B 的权威列表会因这次写入被判过期丢弃，外来会话就一直挂着。
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

    // 只留本项目的新会话，A 的会话不混进来
    expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["b-new"]);
  });

  it("keeps the idempotency key and reconciles the list when a stale rewrite fails in transit", async () => {
    // 网络中断说不清服务端受理没有：换新键重试会真的分叉两次，不对账则受理了的分支
    // 要等刷新页面才出现。
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

    // 切走使本轮作废，随后改写以网络错误告终
    await act(async () => {
      await result.current.switchSession("session-2");
      rewriteDeferred.reject(new Error("network down"));
      await rewriteDeferred.promise.catch(() => {});
    });

    await waitFor(() => {
      expect(listCalls).toBeGreaterThan(callsBefore);
    });

    // 回到原会话重试：沿用同一幂等键，服务端据此认领同一条权威条目而非二次分叉
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

    // 再点同一条会话不再被短路，重试成功，失败提示随之撤下
    await act(async () => {
      await result.current.switchSession("session-2");
    });
    expect(useAssistantStore.getState().turns).toHaveLength(1);
    expect(useAssistantStore.getState().error).toBeNull();
  });

  it("invalidates a pending rewrite as soon as the current session is deleted", async () => {
    mockIdleSession([userEntry(0, "原始消息")]);
    // DELETE 挂起，让改写的受理落在它在途的那段窗口里
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

    // 删除当前会话与改写受理竞速：作废在 DELETE 发出时就已生效，不等它返回
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

    // 迟到的受理不得把用户装到一个分支上
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

    // 删除失败后会话还在：补一次重载，服务端已受理的那一轮才不会停在本地不可见
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
    // 原始输入留在仍开着的编辑器里，重放归它
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

    // 用户不等改写返回就切走：本轮作废，sending 由切换方复位
    await act(async () => {
      await result.current.switchSession("session-3");
    });
    expect(useAssistantStore.getState().sending).toBe(false);

    // 服务端此刻已经取代 session-1、开出分支 session-2
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

    // 迟到的受理不得把用户从他已切去的会话拽到分支
    expect(useAssistantStore.getState().currentSessionId).toBe("session-3");
    expect(openStreamSpy.mock.calls.map(([options]) => options.sessionId)).toEqual(["session-1", "session-3"]);
    // 但服务端的分叉已经发生，列表补拉一次：已消失的 session-1 让位给 session-2
    await waitFor(() => {
      expect(useAssistantStore.getState().sessions.map((s) => s.id)).toEqual(["session-2", "session-3"]);
    });
  });
});
