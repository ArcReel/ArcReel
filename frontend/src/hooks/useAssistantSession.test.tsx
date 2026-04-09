import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import type { AssistantSnapshot, PendingQuestion, SessionMeta } from "@/types";
import { useAssistantSession } from "./useAssistantSession";

class MockEventSource {
  static instances: MockEventSource[] = [];

  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
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
        header: "Output",
        question: "What is the output format?",
        multiSelect: false,
        options: [
          { label: "Summary", description: "Concise output" },
          { label: "Detailed", description: "Full explanation" },
        ],
      },
    ],
  };
}

function makeSnapshot(overrides: Partial<AssistantSnapshot> = {}): AssistantSnapshot {
  return {
    session_id: "session-1",
    status: "idle",
    turns: [],
    draft_turn: null,
    pending_questions: [],
    ...overrides,
  };
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

describe("useAssistantSession", () => {
  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    MockEventSource.instances = [];
    vi.restoreAllMocks();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.spyOn(API, "listAssistantSkills").mockResolvedValue({ skills: [] });
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

  it("restores pendingQuestion from idle snapshots", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(
      makeSnapshot({ pending_questions: [makePendingQuestion()] }),
    );

    renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().pendingQuestion?.question_id).toBe("q-1");
    });
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
  });

  it("submits answers successfully and clears pendingQuestion", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot());
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
      await result.current.answerQuestion("q-1", { "What is the output format?": "Summary" });
    });

    expect(answerSpy).toHaveBeenCalledWith("demo", "session-1", "q-1", {
      "What is the output format?": "Summary",
    });
    expect(useAssistantStore.getState().pendingQuestion).toBeNull();
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
  });

  it("keeps pendingQuestion and surfaces errors when answer submission fails", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot());
    vi.spyOn(API, "answerAssistantQuestion").mockRejectedValue(new Error("Answer submission failed"));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion());
    });

    await act(async () => {
      await result.current.answerQuestion("q-1", { "What is the output format?": "Summary" });
    });

    expect(useAssistantStore.getState().pendingQuestion?.question_id).toBe("q-1");
    expect(useAssistantStore.getState().answeringQuestion).toBe(false);
    expect(useAssistantStore.getState().error).toBe("Answer submission failed");
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
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot());

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    act(() => {
      useAssistantStore.getState().setPendingQuestion(makePendingQuestion());
      useAssistantStore.getState().setAnsweringQuestion(true);
    });

    await act(async () => {
      await result.current.createNewSession();
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

  it("restores session state and removes optimistic turn when send fails", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot());
    vi.spyOn(API, "sendAssistantMessage").mockRejectedValue(new Error("Send failed"));

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(useAssistantStore.getState().sending).toBe(false);
    expect(useAssistantStore.getState().sessionStatus).toBe("idle");
    expect(useAssistantStore.getState().turns).toEqual([]);
    expect(useAssistantStore.getState().error).toBe("Send failed");
    expect(MockEventSource.instances).toHaveLength(0);
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
    vi.spyOn(API, "getAssistantSnapshot").mockImplementation(async (_projectName, sessionId) =>
      makeSnapshot({
        session_id: sessionId,
        turns: sessionId === "session-2"
          ? [{ type: "assistant", content: [{ type: "text", text: "session-2" }] }]
          : [],
      }),
    );
    const deferred = createDeferred<{ session_id: string; status: string }>();
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
      { type: "assistant", content: [{ type: "text", text: "session-2" }] },
    ]);

    await act(async () => {
      deferred.resolve({ session_id: "session-1", status: "running" });
      await deferred.promise;
    });

    expect(MockEventSource.instances).toHaveLength(0);
    expect(useAssistantStore.getState().currentSessionId).toBe("session-2");
  });

  it("drops optimistic turn once snapshot contains the committed user turn", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot());
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({ session_id: "session-1", status: "running" });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      MockEventSource.instances[0].emit("snapshot", makeSnapshot({
        status: "running",
        turns: [
          {
            type: "user",
            uuid: "real-user-1",
            timestamp: "2099-01-01T00:00:00Z",
            content: [{ type: "text", text: "hello" }],
          },
          {
            type: "assistant",
            uuid: "assistant-1",
            timestamp: "2099-01-01T00:00:01Z",
            content: [{ type: "text", text: "hi" }],
          },
        ],
      }));
    });

    expect(useAssistantStore.getState().turns).toEqual([
      {
        type: "user",
        uuid: "real-user-1",
        timestamp: "2099-01-01T00:00:00Z",
        content: [{ type: "text", text: "hello" }],
      },
      {
        type: "assistant",
        uuid: "assistant-1",
        timestamp: "2099-01-01T00:00:01Z",
        content: [{ type: "text", text: "hi" }],
      },
    ]);
  });

  it("keeps optimistic turn when same-text snapshot only contains an older round", async () => {
    vi.spyOn(API, "listAssistantSessions").mockResolvedValue({
      sessions: [makeSession("session-1", "idle")],
    });
    vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession("session-1", "idle") });
    vi.spyOn(API, "getAssistantSnapshot").mockResolvedValue(makeSnapshot({
      turns: [
        {
          type: "user",
          uuid: "old-user-1",
          timestamp: "2026-01-01T00:00:00Z",
          content: [{ type: "text", text: "hello" }],
        },
        {
          type: "assistant",
          uuid: "old-assistant-1",
          timestamp: "2026-01-01T00:00:01Z",
          content: [{ type: "text", text: "first reply" }],
        },
      ],
    }));
    vi.spyOn(API, "sendAssistantMessage").mockResolvedValue({ session_id: "session-1", status: "running" });

    const { result } = renderHook(() => useAssistantSession("demo"));

    await waitFor(() => {
      expect(useAssistantStore.getState().currentSessionId).toBe("session-1");
    });

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    act(() => {
      MockEventSource.instances[0].emit("snapshot", makeSnapshot({
        status: "running",
        turns: [
          {
            type: "user",
            uuid: "old-user-1",
            timestamp: "2026-01-01T00:00:00Z",
            content: [{ type: "text", text: "hello" }],
          },
          {
            type: "assistant",
            uuid: "old-assistant-1",
            timestamp: "2026-01-01T00:00:01Z",
            content: [{ type: "text", text: "first reply" }],
          },
        ],
      }));
    });

    const turns = useAssistantStore.getState().turns;
    expect(turns).toHaveLength(3);
    expect(turns[0].uuid).toBe("old-user-1");
    expect(turns[1].uuid).toBe("old-assistant-1");
    expect(turns[2].uuid?.startsWith("optimistic-")).toBe(true);
  });
});
