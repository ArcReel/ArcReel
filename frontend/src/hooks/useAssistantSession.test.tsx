import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import type { EntriesResponse, PendingQuestion, SessionMeta, TimelineEntry } from "@/types";
import { useAssistantSession } from "./useAssistantSession";

class MockEventSource {
  static instances: MockEventSource[] = [];
  static readonly CLOSED = 2;

  readyState = 0;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = MockEventSource.CLOSED;
  });
  private readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (event: MessageEvent) => void): void {
    const current = this.listeners.get(type) ?? [];
    current.push(cb);
    this.listeners.set(type, current);
  }

  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent;
    const listeners = this.listeners.get(type) ?? [];
    for (const listener of listeners) {
      listener(event);
    }
  }
}

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

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mockIdleSession(entries: TimelineEntry[] = []) {
  vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
    sessions: [makeSession("session-1", "idle")],
  });
  vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
  vi.spyOn(API, "listAssistantEntries").mockResolvedValue(makeEntriesResponse({ entries }));
}

describe("useAssistantSession", () => {
  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    MockEventSource.instances = [];
    localStorage.clear();
    vi.restoreAllMocks();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.spyOn(API, "listAssistantSkills").mockResolvedValue({ skills: [] });
  });

  it("loads idle session timeline from the entries endpoint (cold read)", async () => {
    mockIdleSession([userEntry(0, "历史消息"), { seq: 1, type: "assistant", content: [{ type: "text", text: "回复" }], uuid: "a-1" }]);

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().turns).toHaveLength(2);
    });
    expect(useAssistantStore.getState().turns[0].content[0].text).toBe("历史消息");
    // 非 running 会话不建 SSE 流
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("connects entry stream for running sessions and appends entries in seq order", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      MockEventSource.instances[0].emit("entry", userEntry(0, "hello"));
      MockEventSource.instances[0].emit("entry", {
        seq: 1,
        type: "assistant",
        content: [{ type: "text", text: "hi" }],
        uuid: "a-1",
      });
      // 重复 seq：身份（seq）门槛去重，不做内容比对
      MockEventSource.instances[0].emit("entry", { ...userEntry(1, "重复"), type: "assistant" });
    });

    const state = useAssistantStore.getState();
    expect(state.entries.map((e) => e.seq)).toEqual([0, 1]);
    expect(state.turns.map((t) => t.type)).toEqual(["user", "assistant"]);
  });

  it("applies draft snapshot and rev-gated deltas, then replaces draft by message_id identity", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      // 重连首帧快照：携带累积态 + rev 门槛
      MockEventSource.instances[0].emit("draft", {
        session_id: "session-1",
        draft: { message_id: "msg_1", content: [{ type: "text", text: "部分" }], rev: 5 },
        rev: 5,
      });
      // 订阅间隙重复投递的 delta（rev ≤ 门槛）须被过滤
      MockEventSource.instances[0].emit("delta", {
        message_id: "msg_1",
        delta_type: "text_delta",
        block_index: 0,
        rev: 5,
        text: "重复",
      });
      MockEventSource.instances[0].emit("delta", {
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
      MockEventSource.instances[0].emit("entry", {
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

  it("clears draft and closes stream on terminal status, keeps draft when interrupted", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      MockEventSource.instances[0].emit("draft", {
        session_id: "session-1",
        draft: { message_id: "msg_1", content: [{ type: "text", text: "被中断的回复" }], rev: 1 },
        rev: 1,
      });
      MockEventSource.instances[0].emit("status", { status: "interrupted" });
    });

    // 中断保留 draft（被中断内容不入日志，刷新后自然消失）
    expect(useAssistantStore.getState().draftTurn?.content[0].text).toBe("被中断的回复");
    expect(useAssistantStore.getState().sessionStatus).toBe("interrupted");
    expect(MockEventSource.instances[0].close).toHaveBeenCalled();
  });

  it("writes pendingQuestion from question SSE events", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "running")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "running") });

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      MockEventSource.instances[0].emit("question", makePendingQuestion());
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
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain("/entries/stream");
    // 游标续传：冷订阅从已有条目之后开始
    expect(MockEventSource.instances[0].url).toContain("after=0");
    // client_key 随请求发送
    expect(sendSpy.mock.calls[0][4]).toEqual(expect.any(String));
  });

  it("keeps timeline unchanged on send failure and reuses the idempotency key on retry", async () => {
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败"))
      .mockResolvedValueOnce({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello") });

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
    expect(MockEventSource.instances).toHaveLength(0);

    await act(async () => {
      accepted = await result.current.sendMessage("hello");
    });

    expect(accepted).toBe(true);
    // 同内容重试复用同一幂等键：服务端按键去重，不产生重复
    expect(sendSpy).toHaveBeenCalledTimes(2);
    expect(sendSpy.mock.calls[1][4]).toBe(sendSpy.mock.calls[0][4]);
  });

  it("uses a fresh idempotency key for different content", async () => {
    mockIdleSession();
    const sendSpy = vi
      .spyOn(API, "sendAssistantMessage")
      .mockRejectedValueOnce(new Error("发送失败"))
      .mockResolvedValueOnce({ session_id: "session-1", status: "accepted", entry: userEntry(0, "另一条") });

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
    const deferred = createDeferred<{ session_id: string; status: string; entry: TimelineEntry | null }>();
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
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello") });
      await deferred.promise;
    });

    // 迟到的发送完成不得污染已切换会话的时间线
    expect(MockEventSource.instances).toHaveLength(0);
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
    const deferred = createDeferred<{ session_id: string; status: string; entry: TimelineEntry | null }>();
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
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello") });
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
    const deferred = createDeferred<{ session_id: string; status: string; entry: TimelineEntry | null }>();
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
      deferred.resolve({ session_id: "session-1", status: "accepted", entry: userEntry(0, "hello") });
      await deferred.promise;
    });

    await waitFor(() => {
      expect(sendResult).toBe(false);
    });
  });
});
