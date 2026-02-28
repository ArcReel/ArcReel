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
    sdk_session_id: null,
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
      await result.current.answerQuestion("q-1", { "输出格式是什么？": "摘要" });
    });

    expect(answerSpy).toHaveBeenCalledWith("demo", "session-1", "q-1", {
      "输出格式是什么？": "摘要",
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
});
