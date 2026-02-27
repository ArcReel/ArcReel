import { useCallback, useEffect, useRef } from "react";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import type { Turn, PendingQuestion, SessionMeta } from "@/types";

// ---------------------------------------------------------------------------
// Helpers — 从旧 use-assistant-state.js 移植
// ---------------------------------------------------------------------------

function parseSsePayload(event: MessageEvent): Record<string, unknown> {
  try {
    return JSON.parse(event.data || "{}");
  } catch {
    return {};
  }
}

function applyTurnPatch(prev: Turn[], patch: Record<string, unknown>): Turn[] {
  const op = patch.op as string;
  if (op === "reset") return (patch.turns as Turn[]) ?? [];
  if (op === "append" && patch.turn) return [...prev, patch.turn as Turn];
  if (op === "replace_last" && patch.turn) {
    return prev.length === 0
      ? [patch.turn as Turn]
      : [...prev.slice(0, -1), patch.turn as Turn];
  }
  return prev;
}

const TERMINAL = new Set(["completed", "error", "interrupted"]);

// ---------------------------------------------------------------------------
// localStorage helpers — 记住每个项目最后使用的会话
// ---------------------------------------------------------------------------

const LAST_SESSION_KEY = "arcreel:lastSessionByProject";

function getLastSessionId(projectName: string): string | null {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}");
    return map[projectName] ?? null;
  } catch {
    return null;
  }
}

function saveLastSessionId(projectName: string, sessionId: string): void {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}");
    map[projectName] = sessionId;
    localStorage.setItem(LAST_SESSION_KEY, JSON.stringify(map));
  } catch {
    // 静默失败
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * 管理 AI 助手会话生命周期：
 * - 加载/创建会话
 * - 发送消息
 * - SSE 流式接收
 * - 中断会话
 */
export function useAssistantSession(projectName: string | null) {
  const store = useAssistantStore;
  const streamRef = useRef<EventSource | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<string>("idle");

  // 关闭流
  const closeStream = useCallback(() => {
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
  }, []);

  // 连接 SSE 流
  const connectStream = useCallback(
    (sessionId: string) => {
      closeStream();

      const url = API.getAssistantStreamUrl(sessionId);
      const source = new EventSource(url);
      streamRef.current = source;

      source.addEventListener("snapshot", (event) => {
        const data = parseSsePayload(event as MessageEvent);
        store.getState().setTurns((data.turns as Turn[]) ?? []);
        store.getState().setDraftTurn((data.draft_turn as Turn) ?? null);

        if (typeof data.status === "string") {
          store.getState().setSessionStatus(data.status as "idle");
          statusRef.current = data.status as string;
          if (data.status !== "running") {
            store.getState().setSending(false);
          }
        }

        // pending questions
        const questions = data.pending_questions as Array<Record<string, unknown>> | undefined;
        const pending = questions?.find(
          (q) => q.question_id && Array.isArray(q.questions) && (q.questions as unknown[]).length > 0,
        );
        store.getState().setPendingQuestion(
          pending ? { question_id: pending.question_id as string, questions: pending.questions as PendingQuestion["questions"] } : null,
        );
      });

      source.addEventListener("patch", (event) => {
        const payload = parseSsePayload(event as MessageEvent);
        const patch = (payload.patch ?? payload) as Record<string, unknown>;
        store.getState().setTurns(applyTurnPatch(store.getState().turns, patch));
        if ("draft_turn" in payload) {
          store.getState().setDraftTurn((payload.draft_turn as Turn) ?? null);
        }
      });

      source.addEventListener("delta", (event) => {
        const payload = parseSsePayload(event as MessageEvent);
        if ("draft_turn" in payload) {
          store.getState().setDraftTurn((payload.draft_turn as Turn) ?? null);
        }
      });

      source.addEventListener("status", (event) => {
        const data = parseSsePayload(event as MessageEvent);
        const status = (data.status as string) ?? statusRef.current;
        statusRef.current = status;
        store.getState().setSessionStatus(status as "idle");

        if (TERMINAL.has(status)) {
          store.getState().setSending(false);
          store.getState().setInterrupting(false);
          store.getState().setPendingQuestion(null);
          if (status !== "interrupted") {
            store.getState().setDraftTurn(null);
          }
          closeStream();
        }
      });

      source.addEventListener("question", (event) => {
        const payload = parseSsePayload(event as MessageEvent);
        if (payload.question_id && Array.isArray(payload.questions)) {
          store.getState().setPendingQuestion({
            question_id: payload.question_id as string,
            questions: payload.questions as PendingQuestion["questions"],
          });
        }
      });

      source.onerror = () => {
        if (statusRef.current === "running") {
          reconnectRef.current = setTimeout(() => {
            connectStream(sessionId);
          }, 3000);
        }
      };
    },
    [closeStream, store],
  );

  // 加载会话
  useEffect(() => {
    if (!projectName) return;
    let cancelled = false;

    async function init() {
      store.getState().setMessagesLoading(true);
      try {
        // 获取会话列表
        const res = await API.listAssistantSessions(projectName);
        const sessions = res.sessions ?? [];
        store.getState().setSessions(sessions);

        // 优先使用上次选择的会话（如果仍存在于列表中）
        const lastId = getLastSessionId(projectName!);
        const sessionId = (lastId && sessions.some((s: SessionMeta) => s.id === lastId))
          ? lastId
          : sessions[0]?.id;
        if (!sessionId) {
          store.getState().setCurrentSessionId(null);
          store.getState().setMessagesLoading(false);
          return;
        }
        if (cancelled) return;

        store.getState().setCurrentSessionId(sessionId);

        // 加载会话快照
        const session = await API.getAssistantSession(sessionId);
        const status = (session.session as { status?: string })?.status ?? "idle";
        statusRef.current = status;
        store.getState().setSessionStatus(status as "idle");

        if (status === "running") {
          connectStream(sessionId);
        } else {
          const snapshot = await API.getAssistantSnapshot(sessionId);
          if (cancelled) return;
          store.getState().setTurns((snapshot.turns as Turn[]) ?? []);
          store.getState().setDraftTurn((snapshot.draft_turn as Turn) ?? null);
        }
      } catch {
        // 静默失败
      } finally {
        if (!cancelled) store.getState().setMessagesLoading(false);
      }
    }

    // 加载技能列表
    API.listAssistantSkills(projectName)
      .then((res) => {
        if (!cancelled) store.getState().setSkills(res.skills ?? []);
      })
      .catch(() => {});

    init();

    return () => {
      cancelled = true;
      closeStream();
    };
  }, [projectName, connectStream, closeStream, store]);

  // 发送消息
  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || store.getState().sending) return;

      store.getState().setSending(true);
      store.getState().setError(null);

      try {
        let sessionId = store.getState().currentSessionId;

        // 如果没有会话，创建一个（懒创建：以首条消息作为标题）
        if (!sessionId && projectName) {
          const title = content.trim().slice(0, 30);
          const res = await API.createAssistantSession(projectName, title);
          const raw = res as Record<string, unknown>;
          const sessionObj = (raw.session ?? raw) as Record<string, unknown>;
          sessionId = (sessionObj.id as string) ?? null;
          if (sessionId) {
            const newSession: SessionMeta = {
              id: sessionId,
              sdk_session_id: null,
              transcript_path: null,
              project_name: projectName,
              title,
              status: "idle" as const,
              created_at: (sessionObj.created_at as string) ?? new Date().toISOString(),
              updated_at: (sessionObj.created_at as string) ?? new Date().toISOString(),
            };
            store.getState().setCurrentSessionId(sessionId);
            store.getState().setSessions([newSession, ...store.getState().sessions]);
            store.getState().setIsDraftSession(false);
            saveLastSessionId(projectName, sessionId);
          }
        }

        if (!sessionId) throw new Error("无法创建会话");

        // 发送消息
        await API.sendAssistantMessage(sessionId, content);

        // 连接 SSE 流
        statusRef.current = "running";
        store.getState().setSessionStatus("running");
        connectStream(sessionId);
      } catch (err) {
        store.getState().setError((err as Error).message ?? "发送失败");
        store.getState().setSending(false);
      }
    },
    [projectName, connectStream, store],
  );

  // 中断会话
  const interrupt = useCallback(async () => {
    const sessionId = store.getState().currentSessionId;
    if (!sessionId || statusRef.current !== "running") return;

    store.getState().setInterrupting(true);
    try {
      await API.interruptAssistantSession(sessionId);
    } catch (err) {
      store.getState().setError((err as Error).message ?? "中断失败");
      store.getState().setInterrupting(false);
    }
  }, [store]);

  // 创建新会话（懒创建：仅清空状态，实际创建延迟到首次发消息时）
  const createNewSession = useCallback(async () => {
    if (!projectName) return;

    closeStream();
    store.getState().setTurns([]);
    store.getState().setDraftTurn(null);
    store.getState().setSessionStatus("idle");
    store.getState().setPendingQuestion(null);
    store.getState().setCurrentSessionId(null);
    store.getState().setIsDraftSession(true);
    statusRef.current = "idle";
  }, [projectName, closeStream, store]);

  // 切换到指定会话
  const switchSession = useCallback(async (sessionId: string) => {
    if (store.getState().currentSessionId === sessionId) return;

    closeStream();
    store.getState().setCurrentSessionId(sessionId);
    store.getState().setIsDraftSession(false);
    store.getState().setTurns([]);
    store.getState().setDraftTurn(null);
    store.getState().setPendingQuestion(null);
    store.getState().setMessagesLoading(true);

    // 记住选择
    if (projectName) saveLastSessionId(projectName, sessionId);

    try {
      const res = await API.getAssistantSession(sessionId);
      const raw = res as Record<string, unknown>;
      const sessionObj = (raw.session ?? raw) as Record<string, unknown>;
      const status = (sessionObj.status as string) ?? "idle";
      statusRef.current = status;
      store.getState().setSessionStatus(status as "idle");

      if (status === "running") {
        connectStream(sessionId);
      } else {
        const snapshot = await API.getAssistantSnapshot(sessionId);
        store.getState().setTurns((snapshot.turns as Turn[]) ?? []);
        store.getState().setDraftTurn((snapshot.draft_turn as Turn) ?? null);
      }
    } catch {
      // 静默失败
    } finally {
      store.getState().setMessagesLoading(false);
    }
  }, [projectName, closeStream, connectStream, store]);

  // 删除会话
  const deleteSession = useCallback(async (sessionId: string) => {
    try {
      await API.deleteAssistantSession(sessionId);
      const sessions = store.getState().sessions.filter((s) => s.id !== sessionId);
      store.getState().setSessions(sessions);

      // 如果删除的是当前会话，切换到下一个
      if (store.getState().currentSessionId === sessionId) {
        if (sessions.length > 0) {
          await switchSession(sessions[0].id);
        } else {
          closeStream();
          store.getState().setCurrentSessionId(null);
          store.getState().setTurns([]);
          store.getState().setDraftTurn(null);
          store.getState().setSessionStatus(null);
          statusRef.current = "idle";
        }
      }
    } catch {
      // 静默失败
    }
  }, [closeStream, switchSession, store]);

  return { sendMessage, interrupt, createNewSession, switchSession, deleteSession };
}
