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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TERMINAL = new Set(["completed", "error", "interrupted"]);

function lastEntrySeq(entries: TimelineEntry[]): number {
  return entries.length > 0 ? entries[entries.length - 1].seq : -1;
}

function deletingKey(projectName: string, sessionId: string): string {
  return `${projectName}\n${sessionId}`;
}

// ---------------------------------------------------------------------------
// localStorage helpers remember last-used sessions per project.
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
    // Fail silently.
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Manage Agent-session lifecycles, with event logs as sole timeline truth:
 * - Cold GET entries for historical replay
 * - Live SSE entries, event IDs equal seq, reconnecting with client Last-Event-ID:
 *   open once per session, close on switches/panel closure; terminal turns update state without closing,
 *   receiving both sent and autonomous turns on the same stream
 * - Send even during replies; existing-session messages enter trays until Agent acceptance,
 *   then authoritative streamed entries; new-session initial sends return authoritative entries directly.
 *   Idempotent client_key prevents retry duplicates; timelines never render synthetic local messages
 */
export function useAssistantSession(projectName: string | null) {
  const { t } = useTranslation("dashboard");
  const store = useAssistantStore;
  const streamRef = useRef<SseStreamHandle | null>(null);
  const streamSessionRef = useRef<string | null>(null);
  const statusRef = useRef<string>("idle");
  const streamStatusCursorRef = useRef<{ sessionId: string; seq: number } | null>(null);
  const deferredResumeRef = useRef<{ projectName: string; sessionId: string } | null>(null);
  const pendingSendVersionRef = useRef(0);
  // Failed same-content retries reuse idempotency keys, cleared after success.
  const failedSendRef = useRef<{ clientKey: string; signature: string } | null>(null);
  // IDs edited on this page awaiting returned contents. Keep after failed withdrawal responses; later stream removal
  // returns contents if Agents never processed them. Remove after one return; response/stream races return once only, excluding other pages.
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

  // Session-loading cancellation domains share controllers across init auto-selection and loadSession
  // cold reads/streams. Every selection takeover (switch/new/delete/send-create/project switch)
  // invalidates pending chains first; signals reject late responses before store writes or SSE connections.
  const loadAbortRef = useRef<AbortController | null>(null);
  // Current project cancellation controllers share signals across useCallback boundaries, including connectStream
  // terminal refetches, aborted on project switches/unmounts.
  const projectAbortRef = useRef<AbortController | null>(null);
  // Projects owning controllers: late old-project callbacks may observe new controllers in
  // projectAbortRef; signals alone cannot identify cross-project work.
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

  // Local authoritative session-list write epochs: create/rewrite-branch/delete update immediately,
  // representing confirmed server writes newer than earlier reads. Increment epochs on writes,
  // discarding stale lists so deleted sessions never reappear or replace new branches.
  const sessionsWriteVersionRef = useRef(0);
  // Last successfully loaded sessions; null after failure permits same-session retries.
  const loadedSessionRef = useRef<string | null>(null);
  // In-flight current-session deletion counts by project/session. Skip rewrite-list compensation only when
  // its source session is being deleted: refetch would add branches and deletion cleanup switch into them,
  // defeating invalidation. Per-session counters rather than shared booleans prevent unrelated deletes
  // clearing each other's protection or long-running other-project/session deletes suppressing legitimate
  // branch discovery until page refresh.
  const deletingCurrentRef = useRef<Record<string, number>>({});
  // Record skipped rewrite refetch by the same key; compensate after deletions settle. Server branches exist,
  // both deletion paths address originals only, so without compensation branches stay invisible until refresh.
  const deferredRefreshRef = useRef<Set<string>>(new Set());

  const writeSessions = useCallback((sessions: SessionMeta[]) => {
    // Late old-project callbacks neither write lists nor consume epochs, which would invalidate new-project
    // initial lists and strand sidebars on old sessions.
    if (projectAbortOwnerRef.current !== projectName) return;
    sessionsWriteVersionRef.current += 1;
    // Long-lived panels retain old lists until new responses. Filter foreign sessions when merging,
    // or create/rewrite writes permanently import them, invalidate in-flight authoritative reads,
    // and strand sidebars on sessions unopenable in current projects.
    store.getState().setSessions(sessions.filter((s) => s.project_name === projectName));
  }, [projectName, store]);

  // Refetch session lists under project cancellation domains; switches while waiting must not
  // overwrite new-project lists. Empty lists never write, avoiding failed reads clearing lists.
  const refreshSessions = useCallback(() => {
    if (!projectName) return;
    // Refetch projectName must own cancellation domains. Late rewrite cleanup carries old
    // projectName while refs hold new controllers; borrowing them to read old lists
    // would write unopenable sessions into new-project sidebars.
    if (projectAbortOwnerRef.current !== projectName) return;
    const signal = projectAbortRef.current?.signal;
    if (!signal) return;
    const writeVersion = sessionsWriteVersionRef.current;
    API.listAssistantSessions(projectName, null, { signal })
      .then((res) => {
        if (signal.aborted) return;
        if (projectAbortOwnerRef.current !== projectName) return;
        // Local authoritative writes since read start invalidate this list.
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        const fresh = res.sessions ?? [];
        if (fresh.length > 0) store.getState().setSessions(fresh);
      })
      .catch(() => {/* Fail silently. */});
  }, [projectName, store]);

  // Reconcile invalidated rewrites; defer refetch while the source session is being deleted: it adds branches that
  // deletion cleanup would select, defeating invalidation. Record and compensate after cleanup.
  const reconcileAfterStaleRewrite = useCallback((originSessionId: string) => {
    if (!projectName) return;
    const key = deletingKey(projectName, originSessionId);
    if ((deletingCurrentRef.current[key] ?? 0) > 0) {
      deferredRefreshRef.current.add(key);
      return;
    }
    refreshSessions();
  }, [projectName, refreshSessions]);

  // Close streams, also stopping client reconnections.
  const closeStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    streamSessionRef.current = null;
  }, []);

  // Connect persistent SSE entries, closed only when switching/leaving sessions, including panel closure.
  const connectStream = useCallback(
    (sessionId: string) => {
      // Skip already-connected same-session live handles; streaming clients own reconnections.
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
          // Streams replay existing entries before draft snapshots; snapshot arrival completes history.
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
            // Interruptions retain drafts; unlogged interrupted content naturally disappears after refresh.
            if (status !== "interrupted") {
              store.getState().clearDraft();
            }

            // Refetch SDK summary titles on turn completion, excluding opening terminal snapshots.
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

      // Cold cursors follow existing entries; reconnect clients resume through Last-Event-ID.
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

  // Project recovery/reconnect reconciliation: current turns already arrive through persistent entries;
  // connect only absent streams, with loading sessions connecting in loadSession cleanup.
  const pendingResumeSignals = useAssistantStore((s) => s.sessionResumeSignals);
  useEffect(() => {
    if (pendingResumeSignals.length === 0) return;
    // Consume whole batches; signals left globally would replay as new after project callback rebuilds.
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

  // Load timelines: nonrunning sessions cold-read then attach streams, running sessions
  // replay streams directly. Aborted fetch awaits reject automatically;
  // recheck before stores/streams, guarding abort-after-resolution races.
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
      // Clear stale questions surviving mounts in global zustand stores;
      // streams redeliver pending questions through question events.
      clearPendingQuestion();

      if (status !== "running") {
        const data = await API.listAssistantEntries(projectName!, sessionId, -1, { signal });
        if (signal.aborted) return;
        store.getState().setEntries(data.entries ?? []);
        store.getState().setDraftSnapshot(data.draft ?? null, data.draft_rev ?? 0);
        store.getState().settleHistory();
      }
    } finally {
      // Failed cold reads still connect streams; entry/draft/status snapshots reconstruct sessions.
      const deferred = deferredResumeRef.current;
      const resumed = deferred?.projectName === projectName && deferred.sessionId === sessionId;
      if (!signal.aborted && (sessionLoaded || resumed)) {
        if (resumed) deferredResumeRef.current = null;
        connectStream(sessionId);
      }
    }
  }, [projectName, clearPendingQuestion, connectStream, store]);

  // Load sessions.
  useEffect(() => {
    if (!projectName) {
      // Leaving projects aborts old loading chains in cleanup without successor cleanup;
      // explicitly clear residual loading to avoid permanent true.
      store.getState().setMessagesLoading(false);
      return;
    }
    // Project cancellation aborts only on project switches/unmounts. Session/skill lists are project data;
    // session create/switch/delete while pending must not invalidate them, or slow skill
    // lists disappear and stale session clicks cancel new-project lists without retry.
    const projectAbort = new AbortController();
    projectAbortRef.current = projectAbort;
    projectAbortOwnerRef.current = projectName;
    // Invalidate successful-load markers per project; retaining old session IDs means failed automatic
    // reloads on return appear already loaded, permanently blocking manual retries.
    loadedSessionRef.current = null;

    async function init() {
      // Auto-selection owns loading chains until subsequent user session actions take over.
      const loadSignal = beginSessionLoad();
      store.getState().setMessagesLoading(true);
      // Reset timelines on project switches, matching create/switch/delete, so session/no-session
      // paths start clean. Running cold-stream cursors derive empty reset entries,
      // effectively subscribing from beginning, without previous-project residue or
      // interleaved old timeline entries.
      store.getState().resetTimeline();
      // Previous-project errors never follow into new projects, matching switchSession.
      store.getState().setError(null);
      const writeVersion = sessionsWriteVersionRef.current;
      try {
        // Fetch project session lists even after user actions invalidate auto-selection.
        const res = await API.listAssistantSessions(projectName!, null, { signal: projectAbort.signal });
        if (projectAbort.signal.aborted) return;
        const sessions = res.sessions ?? [];
        // Local create/rewrite/delete writes invalidate whole in-flight lists, not just list writes:
        // deleting noncurrent sessions may leave loading chains active, and stale auto-selection
        // would load already-deleted timelines.
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        store.getState().setSessions(sessions);
        if (loadSignal.aborted) return;

        // Prefer remembered sessions still present in lists.
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
        // Record like switchSession, or clicking selected sessions needlessly rebuilds
        // timelines and loses unsubmitted edit drafts.
        if (!loadSignal.aborted) loadedSessionRef.current = sessionId;
      } catch {
        // Fail silently, including aborted requests.
      } finally {
        // Invalidated loading belongs to successors (switchSession opens/closes its own;
        // createNewSession / deleteSession / sendMessage explicitly reset),
        // so local cleanup must not interrupt successor loads.
        if (!loadSignal.aborted) store.getState().setMessagesLoading(false);
      }
    }

    // Load skill lists.
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

  // Send messages, returning acceptance; failures preserve caller inputs.
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

      // Extract base64.
      const imagePayload = images?.map((img) => ({
        data: img.dataUrl.split(",")[1] ?? "",
        media_type: img.mimeType,
      }));

      // Same-content failed requests reuse idempotency keys; server keys prevent duplicates.
      // Signatures include projectName: singleton panels survive switches, retaining failed
      // clientKey/signature caches. Project dimensions invalidate old keys naturally,
      // preventing same-content reuse in other projects.
      const signature = JSON.stringify([projectName, sessionId, content.trim(), imagePayload ?? []]);
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
        // Accepted sends take over session/timeline ownership, invalidating pending loading
        // so init cannot overwrite currentSessionId or cold reads replace timelines;
        // reset residual loading too.
        abortSessionLoad();
        store.getState().setMessagesLoading(false);

        const returnedSessionId = result.session_id;

        // New sessions update stores.
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

        // Trays consume streams only; HTTP may arrive before another client's earlier sends.
        // Server-logged authoritative response entries deduplicate through seq thresholds.
        if (result.entry) {
          const lastSeq = lastEntrySeq(store.getState().entries);
          if (result.entry.seq > lastSeq + 1) {
            // Seq gaps mean other clients ran turns while unsubscribed; cold-read missing entries first,
            // or cursors past gaps permanently skip intermediate history.
            try {
              const gap = await API.listAssistantEntries(projectName!, sessionId, lastSeq);
              if (store.getState().currentSessionId !== sessionId) return false;
              store.getState().setEntries(gap.entries ?? []);
            } catch {
              // Fail silently; refresh later recovers gaps.
            }
          }
          store.getState().appendEntry(result.entry);
        }
        // Streams may already contain accepted entries/later CLI idle, or messages left queues without
        // acceptance through CLI cancellation/exit; late acceptance never overwrites server state.
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
        // Open sessions reuse streams; new sessions establish initial streams here.
        connectStream(sessionId);
        return true;
      } catch (err) {
        if (pendingSendVersionRef.current !== sendVersion) return false;
        // Failure has no synthetic messages to roll back; retain retry keys only;
        // pending loading chains remain valid and cold reads continue normally.
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

  // Edit/delete queued messages first withdraw from Agents. Successful edit withdrawals append inputs;
  // already accepted messages notify and enter conversations normally.
  const withdrawQueuedMessage = useCallback(
    async (messageId: string, intent: QueuedMessageWithdrawal) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName) return;
      if (intent === "edit") pendingEditsRef.current.add(messageId);
      try {
        const result = await API.withdrawQueuedMessage(projectName, sessionId, messageId, intent);
        // Inputs are shared drafts, accepting this page's withdrawn contents after navigation; session feedback remains scoped.
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

  // Resend unsent messages, letting servers reconnect sessions as needed and requeue.
  const resendQueuedMessage = useCallback(
    async (messageId: string) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId || projectAbortOwnerRef.current !== projectName) return;
      try {
        // Trays consume streams only; response queued may already be superseded by CLI-exit unsent states.
        await API.resendQueuedMessage(projectName, sessionId, messageId);
      } catch (err) {
        if (projectAbortOwnerRef.current !== projectName || store.getState().currentSessionId !== sessionId) return;
        store.getState().setError(errMsg(err, t("queued_message_resend_failed")));
      }
    },
    [projectName, store, t],
  );

  // Send queued messages immediately by interrupting turns; retain trays until acceptance, notifying already-accepted cases.
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

  // Interrupt sessions.
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

  // Lazily create sessions by clearing state, deferring actual creation until initial sends.
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
    // Draft sessions load nothing; reset invalidated-chain loading.
    store.getState().setMessagesLoading(false);
    statusRef.current = "idle";
  }, [projectName, abortSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, store]);

  // Switch sessions.
  const switchSession = useCallback(async (sessionId: string) => {
    // Long-lived panels retain old sessions after projects become null; residual entries
    // must never dispatch null-project requests on clicks.
    if (!projectName) return;
    // Failed prior loads never short-circuit retries, avoiding empty timelines/no SSE forever.
    if (store.getState().currentSessionId === sessionId && loadedSessionRef.current === sessionId) return;

    const signal = beginSessionLoad();
    invalidatePendingSend();
    closeStream();
    store.getState().setCurrentSessionId(sessionId);
    store.getState().setIsDraftSession(false);
    store.getState().resetTimeline();
    clearPendingQuestion();
    // Clear prior failure notices before retries, never covering successfully loaded conversations.
    store.getState().setError(null);
    store.getState().setMessagesLoading(true);

    // Remember selections.
    saveLastSessionId(projectName, sessionId);

    loadedSessionRef.current = null;
    try {
      await loadSession(sessionId, { signal });
      if (!signal.aborted) loadedSessionRef.current = sessionId;
    } catch {
      // Subsequent-switch aborts are not failures; successors clean up. Real failures notify,
      // explaining empty timelines and retry availability.
      if (!signal.aborted) store.getState().setError(t("session_load_failed"));
    } finally {
      // Invalidated loading belongs to successors; never reset their ongoing loads here.
      if (!signal.aborted) store.getState().setMessagesLoading(false);
    }
  }, [projectName, beginSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, loadSession, store, t]);

  // Rewrite historical user messages; return acceptance, preserving edit states/drafts on failure.
  //
  // Keep all views unchanged before acceptance: invalid anchors400, pending questions/
  // superseded originals409 may reject, retaining original sessions/timelines. Only accepted
  // rewrites wholly switch branches, rebuilding timelines/streams without incremental stitching.
  const rewriteMessage = useCallback(
    async (anchorEntryUuid: string, content: string, images?: ImagePayload[]): Promise<boolean> => {
      const originSessionId = store.getState().currentSessionId;
      if (!projectName || !originSessionId) return false;
      // Like sends, images are content; empty-text image messages remain submittable.
      if (!content.trim() && (!images || images.length === 0)) return false;
      // sending locks both sends/rewrites, each starting turns, never concurrent.
      if (store.getState().sending) return false;

      // Share send versions; every selection takeover invalidates pending sends and
      // rewrites, preventing late branches pulling users back from chosen sessions.
      const rewriteVersion = pendingSendVersionRef.current + 1;
      pendingSendVersionRef.current = rewriteVersion;
      store.getState().setSending(true);
      store.getState().setError(null);
      store.getState().setStartupFailure(null);

      // Retry keys recover same messages in new branches, queued/logged, without reforking.
      // Signatures include anchors/rewritten content/attachments; changed contents get new keys.
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

        // Invalidated rounds never pull users back; invalidators reset sending.
        // Servers nevertheless created branches/replaced originals, leaving local lists stale;
        // refetch once so branches appear without page refresh.
        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        failedRewriteRef.current = null;

        const newSessionId = result.session_id;
        // Server lists omit superseded originals; remove locally, replacing with branches.
        // Retain original titles because branches continue conversations; terminal-list refreshes
        // correct through SDK summaries.
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

        // Switch wholly: reset timelines/edit states, cold-read/connect, remember selections,
        // preserving branches through refresh/reconnection.
        await switchSession(newSessionId);
        return true;
      } catch (err) {
        // Retain keys even after invalidation: network failures cannot prove acceptance, and new
        // retry keys actually fork twice. Invalidated requests also reconcile lists, exposing accepted
        // branches without page refresh.
        failedRewriteRef.current = { clientKey, signature };
        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        // Startup failures mirror sends, using failure cards rather than error strips. Mark rewrite origin:
        // still-open editors retain original inputs and own Resend replay.
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

  // Delete sessions, returning API success; failures retain sessions and caller retry notices.
  const deleteSession = useCallback(async (sessionId: string): Promise<boolean> => {
    if (!projectName) return false;
    // Deleting current sessions immediately takes selection ownership, before DELETE returns;
    // accepted in-flight sends/rewrites could otherwise switch branches unnoticed by deletion cleanup.
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

      // Deleted current sessions switch next.
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
          // No-session state loads nothing; reset invalidated-chain loading.
          store.getState().setMessagesLoading(false);
          statusRef.current = "idle";
        }
      }
      return true;
    } catch {
      // Failed deletes retain sessions, but initial invalidation detached pending send/rewrite cleanup;
      // actually accepted rounds become locally invisible while inputs can resend duplicates.
      // Pair invalidation with compensation: reload current sessions to recover them.
      // Compensate deleted targets only; user/project switches may install other current sessions,
      // whose timelines old projectName closures must never overwrite.
      if (invalidatedForDelete && store.getState().currentSessionId === sessionId
        && projectAbortOwnerRef.current === projectName) {
        loadedSessionRef.current = null;
        await switchSession(sessionId);
      }
      return false;
    } finally {
      if (invalidatedForDelete) {
        deletingCurrentRef.current[deleteKey] -= 1;
        // Compensate only after last concurrent deletion settles; earlier remains inside deletion windows.
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
