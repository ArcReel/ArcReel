import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { errMsg, voidCall } from "@/utils/async";
import { AgentFailureError, API } from "@/api";
import { uid } from "@/utils/id";
import type { AttachedImage } from "@/hooks/useImageAttachments";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import type { SseStreamHandle } from "@/utils/sse-stream";
import type {
  DraftDeltaPayload,
  DraftState,
  ImagePayload,
  PendingQuestion,
  QueuedMessage,
  QueuedMessageWithdrawal,
  SessionMeta,
  TimelineEntry,
} from "@/types";


const TERMINAL = new Set(["completed", "error", "interrupted"]);

function lastEntrySeq(entries: TimelineEntry[]): number {
  return entries.length > 0 ? entries[entries.length - 1].seq : -1;
}

function deletingKey(projectName: string, sessionId: string): string {
  return `${projectName}\n${sessionId}`;
}


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

export function useAssistantSession(projectName: string | null) {
  const { t } = useTranslation("dashboard");
  const store = useAssistantStore;
  const streamRef = useRef<SseStreamHandle | null>(null);
  const streamSessionRef = useRef<string | null>(null);
  const statusRef = useRef<string>("idle");
  const streamStatusCursorRef = useRef<{ sessionId: string; seq: number } | null>(null);
  const deferredResumeRef = useRef<{ projectName: string; sessionId: string } | null>(null);
  const pendingSendVersionRef = useRef(0);
  const failedSendRef = useRef<{ clientKey: string; signature: string } | null>(null);
  const pendingEditsRef = useRef(new Set<string>());
  const failedRewriteRef = useRef<{ clientKey: string; signature: string } | null>(null);

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

  const loadAbortRef = useRef<AbortController | null>(null);
  const projectAbortRef = useRef<AbortController | null>(null);
  const projectAbortOwnerRef = useRef<string | null>(null);

  const abortSessionLoad = useCallback(() => {
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
  }, []);

  const beginSessionLoad = useCallback(() => {
    abortSessionLoad();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    return controller.signal;
  }, [abortSessionLoad]);

  const sessionsWriteVersionRef = useRef(0);
  const loadedSessionRef = useRef<string | null>(null);
  const deletingCurrentRef = useRef<Record<string, number>>({});
  const deferredRefreshRef = useRef<Set<string>>(new Set());

  const writeSessions = useCallback((sessions: SessionMeta[]) => {
    if (projectAbortOwnerRef.current !== projectName) return;
    sessionsWriteVersionRef.current += 1;
    store.getState().setSessions(sessions.filter((s) => s.project_name === projectName));
  }, [projectName, store]);

  const refreshSessions = useCallback(() => {
    if (!projectName) return;
    if (projectAbortOwnerRef.current !== projectName) return;
    const signal = projectAbortRef.current?.signal;
    if (!signal) return;
    const writeVersion = sessionsWriteVersionRef.current;
    API.listAssistantSessions(projectName, null, { signal })
      .then((res) => {
        if (signal.aborted) return;
        if (projectAbortOwnerRef.current !== projectName) return;
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        const fresh = res.sessions ?? [];
        if (fresh.length > 0) store.getState().setSessions(fresh);
      })
      .catch(() => { });
  }, [projectName, store]);

  const reconcileAfterStaleRewrite = useCallback((originSessionId: string) => {
    if (!projectName) return;
    const key = deletingKey(projectName, originSessionId);
    if ((deletingCurrentRef.current[key] ?? 0) > 0) {
      deferredRefreshRef.current.add(key);
      return;
    }
    refreshSessions();
  }, [projectName, refreshSessions]);

  const closeStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    streamSessionRef.current = null;
  }, []);

  const connectStream = useCallback(
    (sessionId: string) => {
      if (streamRef.current && streamSessionRef.current === sessionId && !streamRef.current.closed) {
        return;
      }

      closeStream();
      streamSessionRef.current = sessionId;

      const handlers: Record<string, (payload: Record<string, unknown>) => void> = {
        entry(entry) {
          if (typeof entry.seq === "number" && typeof entry.type === "string") {
            store.getState().appendEntry(entry as unknown as TimelineEntry);
          }
        },
        draft(payload) {
          const draft = (payload.draft ?? null) as DraftState | null;
          const rev = typeof payload.rev === "number" ? payload.rev : 0;
          store.getState().setDraftSnapshot(draft, rev);
          store.getState().settleHistory();
        },
        delta(payload) {
          if (typeof payload.message_id === "string" && typeof payload.rev === "number") {
            store.getState().applyDelta(payload as unknown as DraftDeltaPayload);
          }
        },
        status(data) {
          streamStatusCursorRef.current = { sessionId, seq: lastEntrySeq(store.getState().entries) };
          const status = (data.status as string) ?? statusRef.current;
          const wasRunning = statusRef.current === "running";

          statusRef.current = status;
          store.getState().setSessionStatus(status as "idle");

          if (TERMINAL.has(status)) {
            store.getState().setInterrupting(false);
            clearPendingQuestion();
            if (status !== "interrupted") {
              store.getState().clearDraft();
            }

            if (wasRunning) refreshSessions();
          }
        },
        queue(payload) {
          if (Array.isArray(payload.messages)) {
            store.getState().setQueuedMessages(payload.messages.filter(isQueuedMessage));
          }
        },
        queue_upsert(payload) {
          if (isQueuedMessage(payload.message)) store.getState().upsertQueuedMessage(payload.message);
        },
        queue_remove(payload) {
          if (typeof payload.id !== "string") return;
          if (
            pendingEditsRef.current.delete(payload.id) &&
            payload.withdrawn === "edit" &&
            isQueuedMessage(payload.message)
          ) {
            store.getState().appendToComposer(payload.message.content);
          }
          store.getState().removeQueuedMessage(payload.id);
        },
        question(payload) {
          const pendingQuestion = getPendingQuestionFromEvent(payload);
          if (pendingQuestion) {
            syncPendingQuestion(pendingQuestion);
          }
        },
      };

      const after = lastEntrySeq(store.getState().entries);
      const stream = API.openAssistantEntriesStream({
        projectName: projectName!,
        sessionId,
        after,
        onEvent(event, payload) {
          const isActiveStream =
            streamRef.current === stream &&
            streamSessionRef.current === sessionId &&
            store.getState().currentSessionId === sessionId;
          if (!isActiveStream || !Object.hasOwn(handlers, event)) return;
          handlers[event](payload);
        },
      });
      streamRef.current = stream;
    },
    [clearPendingQuestion, projectName, closeStream, refreshSessions, store, syncPendingQuestion],
  );

  const pendingResumeSignals = useAssistantStore((s) => s.sessionResumeSignals);
  useEffect(() => {
    if (pendingResumeSignals.length === 0) return;
    const signals = store.getState().takeSessionResumeSignals();
    const sessionId = store.getState().currentSessionId;
    if (!projectName || !sessionId) return;
    const relevant = signals.some(
      (s) => s.projectName === projectName && (s.kind === "resync" || s.sessionId === sessionId),
    );
    if (!relevant) return;
    if (store.getState().messagesLoading) {
      deferredResumeRef.current = { projectName, sessionId };
      return;
    }
    connectStream(sessionId);
  }, [projectName, pendingResumeSignals, connectStream, store]);

  const loadSession = useCallback(async (sessionId: string, options: { signal: AbortSignal }) => {
    const { signal } = options;
    store.getState().beginHistory();
    let sessionLoaded = false;
    try {
      const res = await API.getAssistantSession(projectName!, sessionId, { signal });
      if (signal.aborted) return;
      sessionLoaded = true;
      const raw = res as Record<string, unknown>;
      const sessionObj = (raw.session ?? raw) as Record<string, unknown>;
      const status = (sessionObj.status as string) ?? "idle";
      statusRef.current = status;
      store.getState().setSessionStatus(status as "idle");
      clearPendingQuestion();

      if (status !== "running") {
        const data = await API.listAssistantEntries(projectName!, sessionId, -1, { signal });
        if (signal.aborted) return;
        store.getState().setEntries(data.entries ?? []);
        store.getState().setDraftSnapshot(data.draft ?? null, data.draft_rev ?? 0);
        store.getState().settleHistory();
      }
    } finally {
      const deferred = deferredResumeRef.current;
      const resumed = deferred?.projectName === projectName && deferred.sessionId === sessionId;
      if (!signal.aborted && (sessionLoaded || resumed)) {
        if (resumed) deferredResumeRef.current = null;
        connectStream(sessionId);
      }
    }
  }, [projectName, clearPendingQuestion, connectStream, store]);

  useEffect(() => {
    if (!projectName) {
      store.getState().setMessagesLoading(false);
      return;
    }
    const projectAbort = new AbortController();
    projectAbortRef.current = projectAbort;
    projectAbortOwnerRef.current = projectName;
    loadedSessionRef.current = null;

    async function init() {
      const loadSignal = beginSessionLoad();
      store.getState().setMessagesLoading(true);
      store.getState().resetTimeline();
      store.getState().setError(null);
      const writeVersion = sessionsWriteVersionRef.current;
      try {
        const res = await API.listAssistantSessions(projectName!, null, { signal: projectAbort.signal });
        if (projectAbort.signal.aborted) return;
        const sessions = res.sessions ?? [];
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        store.getState().setSessions(sessions);
        if (loadSignal.aborted) return;

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

        store.getState().setCurrentSessionId(sessionId);
        await loadSession(sessionId, { signal: loadSignal });
        if (!loadSignal.aborted) loadedSessionRef.current = sessionId;
      } catch {
        // 静默失败（含被 abort 中止的请求）
      } finally {
        if (!loadSignal.aborted) store.getState().setMessagesLoading(false);
      }
    }

    API.listAssistantSkills(projectName, { signal: projectAbort.signal })
      .then((res) => {
        if (projectAbort.signal.aborted) return;
        store.getState().setSkills(res.skills ?? []);
      })
      .catch(() => {});

    voidCall(init());

    return () => {
      projectAbort.abort();
      if (projectAbortRef.current === projectAbort) {
        projectAbortRef.current = null;
        projectAbortOwnerRef.current = null;
        loadedSessionRef.current = null;
      }
      abortSessionLoad();
      invalidatePendingSend();
      closeStream();
    };
  }, [
    projectName,
    abortSessionLoad,
    beginSessionLoad,
    clearPendingQuestion,
    closeStream,
    invalidatePendingSend,
    loadSession,
    store,
  ]);

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
      store.getState().setStartupFailure(null);

      const imagePayload = images?.map((img) => ({
        data: img.dataUrl.split(",")[1] ?? "",
        media_type: img.mimeType,
      }));

      const signature = JSON.stringify([projectName, sessionId, content.trim(), imagePayload ?? []]);
      const clientKey =
        failedSendRef.current?.signature === signature
          ? failedSendRef.current.clientKey
          : uid();

      try {
        const result = await API.sendAssistantMessage(
          projectName!,
          content,
          sessionId,
          imagePayload,
          clientKey,
        );

        if (pendingSendVersionRef.current !== sendVersion) return false;
        failedSendRef.current = null;
        abortSessionLoad();
        store.getState().setMessagesLoading(false);

        const returnedSessionId = result.session_id;

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
          writeSessions([newSession, ...store.getState().sessions]);
          store.getState().setIsDraftSession(false);
          saveLastSessionId(projectName!, returnedSessionId);
          sessionId = returnedSessionId;
        }

        if (store.getState().currentSessionId !== sessionId) return false;

        if (result.entry) {
          const lastSeq = lastEntrySeq(store.getState().entries);
          if (result.entry.seq > lastSeq + 1) {
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
        const acceptedId = result.queued_message?.id;
        const acceptedEntry =
          result.entry ?? (acceptedId ? store.getState().entries.find((e) => e.uuid === acceptedId) : undefined);
        const discarded = !acceptedEntry && acceptedId !== undefined && store.getState().hasLeftQueue(acceptedId);
        const statusCursor = streamStatusCursorRef.current;
        if (
          !discarded &&
          (!acceptedEntry || statusCursor?.sessionId !== sessionId || statusCursor.seq < acceptedEntry.seq)
        ) {
          statusRef.current = "running";
          store.getState().setSessionStatus("running");
        }
        store.getState().setSending(false);
        connectStream(sessionId);
        return true;
      } catch (err) {
        if (pendingSendVersionRef.current !== sendVersion) return false;
        failedSendRef.current = { clientKey, signature };
        if (err instanceof AgentFailureError) {
          store.getState().setStartupFailure(err.failure);
        } else {
          store.getState().setError(errMsg(err, "发送失败"));
        }
        store.getState().setSending(false);
        return false;
      }
    },
    [projectName, abortSessionLoad, connectStream, store, writeSessions],
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

  const withdrawQueuedMessage = useCallback(
    async (messageId: string, intent: QueuedMessageWithdrawal) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName) return;
      if (intent === "edit") pendingEditsRef.current.add(messageId);
      try {
        const result = await API.withdrawQueuedMessage(projectName, sessionId, messageId, intent);
        if (result.message && pendingEditsRef.current.delete(messageId)) {
          store.getState().appendToComposer(result.message.content);
        }
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        if (result.outcome === "accepted") {
          useAppStore.getState().pushToast(t("queued_message_already_accepted"), "info");
          return;
        }
        store.getState().removeQueuedMessage(messageId);
      } catch (err) {
        pendingEditsRef.current.delete(messageId);
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        store.getState().setError(errMsg(err, t("queued_message_withdraw_failed")));
      }
    },
    [projectName, store, t],
  );

  const resendQueuedMessage = useCallback(
    async (messageId: string) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName) return;
      try {
        await API.resendQueuedMessage(projectName, sessionId, messageId);
      } catch (err) {
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        store.getState().setError(errMsg(err, t("queued_message_resend_failed")));
      }
    },
    [projectName, store, t],
  );

  const sendQueuedMessageNow = useCallback(
    async (messageId: string) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName) return;
      try {
        const result = await API.sendQueuedMessageNow(projectName, sessionId, messageId);
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        if (result.outcome === "accepted") useAppStore.getState().pushToast(t("queued_message_already_accepted"), "info");
      } catch (err) {
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        store.getState().setError(errMsg(err, t("queued_message_send_now_failed")));
      }
    },
    [projectName, store, t],
  );

  const interrupt = useCallback(async () => {
    const sessionId = store.getState().currentSessionId;
    if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName || statusRef.current !== "running") return;

    store.getState().setInterrupting(true);
    try {
      await API.interruptAssistantSession(projectName, sessionId);
    } catch (err) {
      if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
      store.getState().setError(errMsg(err, "中断失败"));
      store.getState().setInterrupting(false);
    }
  }, [projectName, store]);

  const createNewSession = useCallback(() => {
    if (!projectName) return;

    abortSessionLoad();
    invalidatePendingSend();
    closeStream();
    store.getState().resetTimeline();
    store.getState().setError(null);
    store.getState().setSessionStatus("idle");
    clearPendingQuestion();
    store.getState().setCurrentSessionId(null);
    store.getState().setIsDraftSession(true);
    store.getState().setMessagesLoading(false);
    statusRef.current = "idle";
  }, [projectName, abortSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, store]);

  const switchSession = useCallback(async (sessionId: string) => {
    if (!projectName) return;
    if (store.getState().currentSessionId === sessionId && loadedSessionRef.current === sessionId) return;

    const signal = beginSessionLoad();
    invalidatePendingSend();
    closeStream();
    store.getState().setCurrentSessionId(sessionId);
    store.getState().setIsDraftSession(false);
    store.getState().resetTimeline();
    clearPendingQuestion();
    store.getState().setError(null);
    store.getState().setMessagesLoading(true);

    saveLastSessionId(projectName, sessionId);

    loadedSessionRef.current = null;
    try {
      await loadSession(sessionId, { signal });
      if (!signal.aborted) loadedSessionRef.current = sessionId;
    } catch {
      if (!signal.aborted) store.getState().setError(t("session_load_failed"));
    } finally {
      if (!signal.aborted) store.getState().setMessagesLoading(false);
    }
  }, [projectName, beginSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, loadSession, store, t]);

  const rewriteMessage = useCallback(
    async (anchorEntryUuid: string, content: string, images?: ImagePayload[]): Promise<boolean> => {
      const originSessionId = store.getState().currentSessionId;
      if (!projectName || !originSessionId) return false;
      if (!content.trim() && (!images || images.length === 0)) return false;
      if (store.getState().sending) return false;

      const rewriteVersion = pendingSendVersionRef.current + 1;
      pendingSendVersionRef.current = rewriteVersion;
      store.getState().setSending(true);
      store.getState().setError(null);
      store.getState().setStartupFailure(null);

      const signature = JSON.stringify([
        projectName,
        originSessionId,
        anchorEntryUuid,
        content.trim(),
        images ?? [],
      ]);
      const clientKey =
        failedRewriteRef.current?.signature === signature
          ? failedRewriteRef.current.clientKey
          : uid();

      try {
        const result = await API.rewriteAssistantMessage(
          projectName,
          originSessionId,
          anchorEntryUuid,
          content,
          images,
          clientKey,
        );

        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        failedRewriteRef.current = null;

        const newSessionId = result.session_id;
        const sessions = store.getState().sessions;
        const origin = sessions.find((s) => s.id === originSessionId);
        const branch: SessionMeta = {
          id: newSessionId,
          project_name: projectName,
          title: origin?.title ?? "",
          status: "running",
          created_at: origin?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        writeSessions([
          branch,
          ...sessions.filter((s) => s.id !== originSessionId && s.id !== newSessionId),
        ]);
        store.getState().setSending(false);

        await switchSession(newSessionId);
        return true;
      } catch (err) {
        failedRewriteRef.current = { clientKey, signature };
        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        if (err instanceof AgentFailureError) {
          store.getState().setStartupFailure(err.failure, "rewrite");
        } else {
          store.getState().setError(errMsg(err, t("message_rewrite_failed")));
        }
        store.getState().setSending(false);
        return false;
      }
    },
    [projectName, reconcileAfterStaleRewrite, switchSession, store, t, writeSessions],
  );

  const deleteSession = useCallback(async (sessionId: string): Promise<boolean> => {
    if (!projectName) return false;
    const invalidatedForDelete = store.getState().currentSessionId === sessionId;
    const deleteKey = deletingKey(projectName, sessionId);
    if (invalidatedForDelete) {
      invalidatePendingSend();
      deletingCurrentRef.current[deleteKey] = (deletingCurrentRef.current[deleteKey] ?? 0) + 1;
    }
    try {
      await API.deleteAssistantSession(projectName, sessionId);
      const sessions = store.getState().sessions.filter((s) => s.id !== sessionId);
      writeSessions(sessions);

      if (store.getState().currentSessionId === sessionId) {
        if (sessions.length > 0) {
          await switchSession(sessions[0].id);
        } else {
          abortSessionLoad();
          invalidatePendingSend();
          closeStream();
          store.getState().setCurrentSessionId(null);
          store.getState().resetTimeline();
          store.getState().setError(null);
          store.getState().setSessionStatus(null);
          clearPendingQuestion();
          store.getState().setMessagesLoading(false);
          statusRef.current = "idle";
        }
      }
      return true;
    } catch {
      if (invalidatedForDelete && store.getState().currentSessionId === sessionId
        && projectAbortOwnerRef.current === projectName) {
        loadedSessionRef.current = null;
        await switchSession(sessionId);
      }
      return false;
    } finally {
      if (invalidatedForDelete) {
        deletingCurrentRef.current[deleteKey] -= 1;
        if (deletingCurrentRef.current[deleteKey] === 0 && deferredRefreshRef.current.delete(deleteKey)) {
          refreshSessions();
        }
      }
    }
  }, [
    projectName,
    abortSessionLoad,
    clearPendingQuestion,
    closeStream,
    invalidatePendingSend,
    refreshSessions,
    switchSession,
    store,
    writeSessions,
  ]);

  return {
    sendMessage,
    rewriteMessage,
    answerQuestion,
    interrupt,
    createNewSession,
    switchSession,
    deleteSession,
    withdrawQueuedMessage,
    resendQueuedMessage,
    sendQueuedMessageNow,
  };
}

function isQueuedMessage(value: unknown): value is QueuedMessage {
  if (typeof value !== "object" || value === null) return false;
  const message = value as Record<string, unknown>;
  return typeof message.id === "string" && Array.isArray(message.content) && typeof message.state === "string";
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
