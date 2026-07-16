import { useCallback, useEffect, useRef } from "react";
import { errMsg, voidCall } from "@/utils/async";
import { API } from "@/api";
import { uid } from "@/utils/id";
import { useAssistantStore } from "@/stores/assistant-store";
import type {
  DraftDeltaPayload,
  DraftState,
  PendingQuestion,
  SessionMeta,
  TimelineEntry,
} from "@/types";

export interface AttachedImage {
  id: string;
  dataUrl: string;
  mimeType: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseSsePayload(event: MessageEvent): Record<string, unknown> {
  try {
    return JSON.parse(String(event.data || "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const TERMINAL = new Set(["completed", "error", "interrupted"]);

function lastEntrySeq(entries: TimelineEntry[]): number {
  return entries.length > 0 ? entries[entries.length - 1].seq : -1;
}

// ---------------------------------------------------------------------------
// localStorage helpers — 记住每个项目最后使用的会话
// ---------------------------------------------------------------------------

const LAST_SESSION_KEY = "arcreel:lastSessionByProject";

function getLastSessionId(projectName: string): string | null {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}") as Record<string, unknown>;
    const value = map[projectName];
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}

function saveLastSessionId(projectName: string, sessionId: string): void {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}") as Record<string, unknown>;
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
 * 管理 AI 助手会话生命周期，时间线唯一读源为会话事件日志：
 * - 冷读 GET entries（历史回放）
 * - SSE entry 流实时接收（事件 id 即 seq，断线按游标续传）
 * - 发送消息：服务端先写日志分配身份，响应回传权威条目；
 *   client_key 幂等，重试不产生重复；不渲染本地合成消息
 */
export function useAssistantSession(projectName: string | null) {
  const store = useAssistantStore;
  const streamRef = useRef<EventSource | null>(null);
  const streamSessionRef = useRef<string | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<string>("idle");
  const pendingSendVersionRef = useRef(0);
  // 失败重试复用同一幂等键（同内容签名），成功后清除
  const failedSendRef = useRef<{ clientKey: string; signature: string } | null>(null);

  const syncPendingQuestion = useCallback((question: PendingQuestion | null) => {
    store.getState().setPendingQuestion(question);
    store.getState().setAnsweringQuestion(false);
  }, [store]);

  const clearPendingQuestion = useCallback(() => {
    syncPendingQuestion(null);
  }, [syncPendingQuestion]);

  const invalidatePendingSend = useCallback(() => {
    pendingSendVersionRef.current += 1;
    store.getState().setSending(false);
  }, [store]);

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
    streamSessionRef.current = null;
  }, []);

  // 连接 SSE entry 流
  const connectStream = useCallback(
    (sessionId: string) => {
      // 如果已连接到同一 session 且连接健康，跳过重连
      if (
        streamRef.current &&
        streamSessionRef.current === sessionId &&
        streamRef.current.readyState !== EventSource.CLOSED
      ) {
        return;
      }

      closeStream();
      streamSessionRef.current = sessionId;

      // 冷订阅游标：已有条目之后；浏览器自动重连由 Last-Event-ID 续传
      const after = lastEntrySeq(store.getState().entries);
      const url = API.getAssistantEntriesStreamUrl(projectName!, sessionId, after);
      const source = new EventSource(url);
      streamRef.current = source;
      const isActiveStream = () =>
        streamRef.current === source &&
        streamSessionRef.current === sessionId &&
        store.getState().currentSessionId === sessionId;

      source.addEventListener("entry", (event) => {
        if (!isActiveStream()) return;
        const entry = parseSsePayload(event);
        if (typeof entry.seq === "number" && typeof entry.type === "string") {
          store.getState().appendEntry(entry as unknown as TimelineEntry);
        }
      });

      source.addEventListener("draft", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        const draft = (payload.draft ?? null) as DraftState | null;
        const rev = typeof payload.rev === "number" ? payload.rev : 0;
        store.getState().setDraftSnapshot(draft, rev);
      });

      source.addEventListener("delta", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        if (typeof payload.message_id === "string" && typeof payload.rev === "number") {
          store.getState().applyDelta(payload as unknown as DraftDeltaPayload);
        }
      });

      source.addEventListener("status", (event) => {
        if (!isActiveStream()) return;
        const data = parseSsePayload(event);
        const status = (data.status as string) ?? statusRef.current;

        statusRef.current = status;
        store.getState().setSessionStatus(status as "idle");

        if (TERMINAL.has(status)) {
          store.getState().setSending(false);
          store.getState().setInterrupting(false);
          clearPendingQuestion();
          // 中断时保留 draft：被中断的流式内容不入日志，刷新后自然消失
          if (status !== "interrupted") {
            store.getState().clearDraft();
          }
          closeStream();

          // Turn 结束后刷新会话列表，获取 SDK summary 标题
          if (projectName) {
            API.listAssistantSessions(projectName).then((res) => {
              const fresh = res.sessions ?? [];
              if (fresh.length > 0) store.getState().setSessions(fresh);
            }).catch(() => {/* 静默失败 */});
          }
        }
      });

      source.addEventListener("question", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        const pendingQuestion = getPendingQuestionFromEvent(payload);
        if (pendingQuestion) {
          syncPendingQuestion(pendingQuestion);
        }
      });

      source.onerror = () => {
        if (!isActiveStream()) return;
        // 浏览器原生自动重连携带 Last-Event-ID 续传；此处仅兜底
        // 连接被判死（CLOSED）的场景，运行中或发送中才重建。
        if (
          source.readyState === EventSource.CLOSED &&
          (statusRef.current === "running" || store.getState().sending)
        ) {
          reconnectRef.current = setTimeout(() => {
            // 自引用 SSE 重连：useEffectEvent 不允许在 setTimeout 内调用，
            // 用 ref 中转又被 immutability 规则禁止。当前写法是延迟到下一 tick
            // 才执行，闭包内的 connectStream 引用已稳定，行为正确。
            // eslint-disable-next-line react-hooks/immutability
            connectStream(sessionId);
          }, 3000);
        }
      };
    },
    [clearPendingQuestion, projectName, closeStream, store, syncPendingQuestion],
  );

  // 加载指定会话时间线：非 running 冷读日志；running 交给 entry 流回放
  const loadSession = useCallback(async (sessionId: string) => {
    const res = await API.getAssistantSession(projectName!, sessionId);
    const raw = res as Record<string, unknown>;
    const sessionObj = (raw.session ?? raw) as Record<string, unknown>;
    const status = (sessionObj.status as string) ?? "idle";
    statusRef.current = status;
    store.getState().setSessionStatus(status as "idle");
    // 清掉跨挂载残留的过期问题（zustand 全局 store 在组件卸载后仍保留）；
    // running 会话的未决问题由 entry 流的 question 事件重新投递。
    clearPendingQuestion();

    if (status === "running") {
      connectStream(sessionId);
    } else {
      const data = await API.listAssistantEntries(projectName!, sessionId);
      if (store.getState().currentSessionId !== sessionId) return;
      store.getState().setEntries(data.entries ?? []);
      store.getState().setDraftSnapshot(data.draft ?? null, data.draft_rev ?? 0);
    }
  }, [projectName, clearPendingQuestion, connectStream, store]);

  // 加载会话
  useEffect(() => {
    if (!projectName) return;
    let cancelled = false;

    async function init() {
      store.getState().setMessagesLoading(true);
      // 切项目先重置时间线（与新建/切换/删除三条会话路径同口径），使有会话/
      // 无会话两个分支都从干净状态出发：running 会话的 SSE 冷订阅游标由重置后
      // 的空 entries 推导（等效从头订阅），不被上一个项目的残留条目污染，也不会
      // 把旧项目条目混排进新会话时间线。
      store.getState().resetTimeline();
      try {
        // 获取会话列表
        const res = await API.listAssistantSessions(projectName!);
        if (cancelled) return;
        const sessions = res.sessions ?? [];
        store.getState().setSessions(sessions);

        // 优先使用上次选择的会话（如果仍存在于列表中）
        const lastId = getLastSessionId(projectName!);
        const sessionId = (lastId && sessions.some((s: SessionMeta) => s.id === lastId))
          ? lastId
          : sessions[0]?.id;
        if (!sessionId) {
          store.getState().setCurrentSessionId(null);
          clearPendingQuestion();
          store.getState().setMessagesLoading(false);
          return;
        }
        if (cancelled) return;

        store.getState().setCurrentSessionId(sessionId);
        await loadSession(sessionId);
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

    voidCall(init());

    return () => {
      cancelled = true;
      invalidatePendingSend();
      closeStream();
    };
  }, [
    projectName,
    clearPendingQuestion,
    closeStream,
    invalidatePendingSend,
    loadSession,
    store,
  ]);

  // 发送消息。返回是否受理成功——失败时调用方保留输入内容。
  const sendMessage = useCallback(
    async (content: string, images?: AttachedImage[]): Promise<boolean> => {
      if ((!content.trim() && (!images || images.length === 0)) || store.getState().sending) {
        return false;
      }

      const sendVersion = pendingSendVersionRef.current + 1;
      pendingSendVersionRef.current = sendVersion;
      let sessionId = store.getState().currentSessionId;
      store.getState().setSending(true);
      store.getState().setError(null);

      // 提取 base64 数据
      const imagePayload = images?.map((img) => ({
        data: img.dataUrl.split(",")[1] ?? "",
        media_type: img.mimeType,
      }));

      // 请求侧幂等键：同一内容失败重试复用同键，服务端按键去重不产生重复
      const signature = JSON.stringify([sessionId, content.trim(), imagePayload ?? []]);
      const clientKey =
        failedSendRef.current?.signature === signature
          ? failedSendRef.current.clientKey
          : uid();

      try {
        const result = await API.sendAssistantMessage(
          projectName!,
          content,
          sessionId,  // null for new session
          imagePayload,
          clientKey,
        );

        if (pendingSendVersionRef.current !== sendVersion) return false;
        failedSendRef.current = null;

        const returnedSessionId = result.session_id;

        // 新会话：更新 store
        if (!sessionId) {
          const newSession: SessionMeta = {
            id: returnedSessionId,
            project_name: projectName!,
            title: content.trim().slice(0, 30) || "图片消息",
            status: "running",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          store.getState().setCurrentSessionId(returnedSessionId);
          store.getState().setSessions([newSession, ...store.getState().sessions]);
          store.getState().setIsDraftSession(false);
          saveLastSessionId(projectName!, returnedSessionId);
          sessionId = returnedSessionId;
        }

        if (store.getState().currentSessionId !== sessionId) return false;

        // 响应携带的权威条目（服务端已写日志分配身份），seq 门槛去重
        if (result.entry) {
          const lastSeq = lastEntrySeq(store.getState().entries);
          if (result.entry.seq > lastSeq + 1) {
            // seq 跳档：其他客户端在本地未订阅期间产生了轮次，先冷读补齐缺口，
            // 否则订阅游标越过缺口后中间条目永远不会被拉取
            try {
              const gap = await API.listAssistantEntries(projectName!, sessionId, lastSeq);
              if (store.getState().currentSessionId !== sessionId) return false;
              store.getState().setEntries(gap.entries ?? []);
            } catch {
              // 静默失败：缺口留待刷新兜底
            }
          }
          store.getState().appendEntry(result.entry);
        }
        statusRef.current = "running";
        store.getState().setSessionStatus("running");
        store.getState().setSending(false);
        connectStream(sessionId);
        return true;
      } catch (err) {
        if (pendingSendVersionRef.current !== sendVersion) return false;
        // 失败：无本地合成消息可回滚，仅记录幂等键供重试复用
        failedSendRef.current = { clientKey, signature };
        store.getState().setError(errMsg(err, "发送失败"));
        store.getState().setSending(false);
        return false;
      }
    },
    [projectName, connectStream, store],
  );

  const answerQuestion = useCallback(
    async (questionId: string, answers: Record<string, string>) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId) return;

      store.getState().setError(null);
      store.getState().setAnsweringQuestion(true);

      try {
        await API.answerAssistantQuestion(projectName, sessionId, questionId, answers);
        store.getState().setPendingQuestion(null);
      } catch (err) {
        store.getState().setError(errMsg(err, "回答失败"));
      } finally {
        store.getState().setAnsweringQuestion(false);
      }
    },
    [projectName, store],
  );

  // 中断会话
  const interrupt = useCallback(async () => {
    const sessionId = store.getState().currentSessionId;
    if (!projectName || !sessionId || statusRef.current !== "running") return;

    store.getState().setInterrupting(true);
    try {
      await API.interruptAssistantSession(projectName, sessionId);
    } catch (err) {
      store.getState().setError(errMsg(err, "中断失败"));
      store.getState().setInterrupting(false);
    }
  }, [projectName, store]);

  // 创建新会话（懒创建：仅清空状态，实际创建延迟到首次发消息时）
  const createNewSession = useCallback(() => {
    if (!projectName) return;

    invalidatePendingSend();
    closeStream();
    store.getState().resetTimeline();
    store.getState().setSessionStatus("idle");
    clearPendingQuestion();
    store.getState().setCurrentSessionId(null);
    store.getState().setIsDraftSession(true);
    statusRef.current = "idle";
  }, [projectName, clearPendingQuestion, closeStream, invalidatePendingSend, store]);

  // 切换到指定会话
  const switchSession = useCallback(async (sessionId: string) => {
    if (store.getState().currentSessionId === sessionId) return;

    invalidatePendingSend();
    closeStream();
    store.getState().setCurrentSessionId(sessionId);
    store.getState().setIsDraftSession(false);
    store.getState().resetTimeline();
    clearPendingQuestion();
    store.getState().setMessagesLoading(true);

    // 记住选择
    if (projectName) saveLastSessionId(projectName, sessionId);

    try {
      await loadSession(sessionId);
    } catch {
      // 静默失败
    } finally {
      store.getState().setMessagesLoading(false);
    }
  }, [projectName, clearPendingQuestion, closeStream, invalidatePendingSend, loadSession, store]);

  // 删除会话
  const deleteSession = useCallback(async (sessionId: string) => {
    if (!projectName) return;
    try {
      await API.deleteAssistantSession(projectName, sessionId);
      const sessions = store.getState().sessions.filter((s) => s.id !== sessionId);
      store.getState().setSessions(sessions);

      // 如果删除的是当前会话，切换到下一个
      if (store.getState().currentSessionId === sessionId) {
        if (sessions.length > 0) {
          await switchSession(sessions[0].id);
        } else {
          invalidatePendingSend();
          closeStream();
          store.getState().setCurrentSessionId(null);
          store.getState().resetTimeline();
          store.getState().setSessionStatus(null);
          clearPendingQuestion();
          statusRef.current = "idle";
        }
      }
    } catch {
      // 静默失败
    }
  }, [projectName, clearPendingQuestion, closeStream, invalidatePendingSend, switchSession, store]);

  return { sendMessage, answerQuestion, interrupt, createNewSession, switchSession, deleteSession };
}

function getPendingQuestionFromEvent(payload: Record<string, unknown>): PendingQuestion | null {
  if (!(typeof payload.question_id === "string" && Array.isArray(payload.questions))) {
    return null;
  }

  return {
    question_id: payload.question_id,
    questions: payload.questions as PendingQuestion["questions"],
  };
}
