import { useCallback, useEffect, useRef } from "react";
import { API } from "@/api";
import { useAssistantStore } from "@/stores/assistant-store";
import type { Turn, PendingQuestion } from "@/types";

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

        const sessionId = sessions[0]?.id;
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

        // 如果没有会话，创建一个
        if (!sessionId && projectName) {
          const res = await API.createAssistantSession(projectName, "");
          sessionId = res.session.id;
          store.getState().setCurrentSessionId(sessionId);
          store.getState().setSessions([res.session, ...store.getState().sessions]);
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

  return { sendMessage, interrupt };
}
