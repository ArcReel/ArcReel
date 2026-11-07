import { create } from "zustand";
import type {
  ContentBlock,
  DraftDeltaPayload,
  DraftState,
  FailureObservation,
  PendingQuestion,
  QueuedMessage,
  SessionMeta,
  SessionStatus,
  SkillInfo,
  TimelineEntry,
  Turn,
} from "@/types";
import {
  applyDraftDelta,
  buildDraftTurn,
  collectCommittedMessageIds,
  createTimelineProjector,
  isDraftReplaced,
  mergeEntriesBySeq,
  type DraftMirror,
} from "@/utils/entry-projection";

/** Entry causing startup failure, determining which input failure-card retries replay. */
export type StartupFailureOrigin = "send" | "rewrite";

export type SessionResumeSignal =
  | { kind: "resumed"; projectName: string; sessionId: string }
  | { kind: "resync"; projectName: string };

interface AssistantState {
  // Sessions
  sessions: SessionMeta[];
  currentSessionId: string | null;
  sessionsLoading: boolean;

  // Timeline (sole event-log read source).
  entries: TimelineEntry[];
  draft: DraftMirror | null;
  draftRev: number;
  // Derived from entries/draft projections, written only by timeline actions.
  turns: Turn[];
  draftTurn: Turn | null;
  messagesLoading: boolean;
  /**
   * Last existing entry seq when sessions load: entries at/below are prior history, higher ones arrive during
   * viewing; failure cards announce only the latter. null means history is still replaying (running sessions
   * resend existing entries through streams); all arriving entries then count as history. Sessions without loading (new/draft) use -1.
   */
  historySeq: number | null;
  /** Queued-message tray: sent messages not yet accepted by Agents, in send order, outside timelines. */
  queuedMessages: QueuedMessage[];

  // Input
  input: string;
  /** Message contents awaiting input-field append (returned while editing queued messages), consumed by input fields. */
  composerAppends: ContentBlock[][];
  sending: boolean;
  interrupting: boolean;
  error: string | null;
  /** Latest observed Agent-startup failure during current panel lifetimes, never persisted across refreshes. */
  startupFailure: FailureObservation | null;
  /**
   * Entry causing this startup failure. Failure-card retries replay only entries still retaining original inputs:
   * `send` retains main-input text; `rewrite` retains the still-open inline editor.
   */
  startupFailureOrigin: StartupFailureOrigin | null;

  // Session status
  sessionStatus: SessionStatus | null;
  sessionStatusDetail: string | null;

  // Questions
  pendingQuestion: PendingQuestion | null;
  answeringQuestion: boolean;

  // Skills
  skills: SkillInfo[];
  skillsLoading: boolean;

  /**
   * User message currently edited inline (entry uuid), at most one simultaneously.
   * Store ownership rather than component-local state enforces the one-editor constraint across whole timelines.
   */
  editingTurnUuid: string | null;

  // Scope
  currentProject: string | null;

  // Draft session (lazy creation)
  isDraftSession: boolean;

  /**
   * Pending session-recovery signals from project streams, batch-consumed by session hooks. resumed: sessions independently
   * return running without sends; resync: streams (re)connect, potentially requiring missed-recovery reconciliation.
   * Queue rather than retain only latest signals: multiple same-batch signals can arrive before hooks consume them, overwriting earlier ones.
   */
  sessionResumeSignals: SessionResumeSignal[];

  // Actions
  setSessions: (sessions: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setSessionsLoading: (loading: boolean) => void;
  /** Replace entire log-entry frames (cold reads / session switches). */
  setEntries: (entries: TimelineEntry[]) => void;
  /** Append authoritative entries, deduplicating seq thresholds by identity, never content. */
  appendEntry: (entry: TimelineEntry) => void;
  /** Initial draft snapshots (reconnect cumulative state + rev filtering thresholds). */
  setDraftSnapshot: (draft: DraftState | null, rev: number) => void;
  /** Apply streaming deltas, ignoring rev values not beyond thresholds. */
  applyDelta: (payload: DraftDeltaPayload) => void;
  clearDraft: () => void;
  /** Clear timelines (project switches / new sessions). */
  resetTimeline: () => void;
  setMessagesLoading: (loading: boolean) => void;
  /** Begin session-history loading: arriving entries count as history until settleHistory. */
  beginHistory: () => void;
  /** Settle history loading at current last entries, unchanged if already bounded. */
  settleHistory: () => void;
  /** Replace entire queued-message sets (entry-stream initial snapshots). */
  setQueuedMessages: (messages: QueuedMessage[]) => void;
  /** Add/update queued messages, never readd messages that left queues. */
  upsertQueuedMessage: (message: QueuedMessage) => void;
  /** Remove queued messages (accepted or discarded). */
  removeQueuedMessage: (id: string) => void;
  /** Whether messages left queues through acceptance/discard. */
  hasLeftQueue: (id: string) => boolean;
  setInput: (input: string) => void;
  /** Hand message contents to input fields, appended after existing content. */
  appendToComposer: (content: ContentBlock[]) => void;
  /** Input fields consume pending append contents. */
  takeComposerAppends: () => ContentBlock[][];
  setSending: (sending: boolean) => void;
  setInterrupting: (interrupting: boolean) => void;
  setError: (error: string | null) => void;
  setStartupFailure: (failure: FailureObservation | null, origin?: StartupFailureOrigin) => void;
  setSessionStatus: (status: SessionStatus | null) => void;
  setSessionStatusDetail: (detail: string | null) => void;
  setPendingQuestion: (question: PendingQuestion | null) => void;
  setAnsweringQuestion: (answering: boolean) => void;
  setSkills: (skills: SkillInfo[]) => void;
  setSkillsLoading: (loading: boolean) => void;
  setEditingTurnUuid: (uuid: string | null) => void;
  setCurrentProject: (project: string | null) => void;
  setIsDraftSession: (draft: boolean) => void;
  notifySessionResumed: (projectName: string, sessionId: string) => void;
  requestSessionResync: (projectName: string) => void;
  /** Consume/clear pending recovery signals. */
  takeSessionResumeSignals: () => SessionResumeSignal[];
}

export const useAssistantStore = create<AssistantState>((set, get) => {
  // Projection-derived caches, not reactive state: projector incrementally folds entries→turns;
  // committedIds indexes draft replacement in O(1). Both self-heal against entries references:
  // external whole-frame setState (for example test reset) can replace entries without store actions;
  // reference mismatches rebuild on next reads, avoiding stale message_id misclassifying draft replacement
  // or stale projector internals misclassifying incremental prefix continuity. Projector prefix checks
  // compare first/last entry references only (O(1), never full arrays: every append otherwise appears
  // changed and defeats incremental work). Container-reference checks add a coarser but more reliable
  // self-healing defense, identical to committedSource. Every legitimate mutation
  // synchronizes xxxSource to references returned by the next get().entries; only external
  // whole-frame replacements bypassing actions trigger it.
  let projector = createTimelineProjector();
  let projectorSource: TimelineEntry[] | null = null;
  let committedIds = new Set<string>();
  let committedSource: TimelineEntry[] | null = null;
  // Message IDs that left queues: send responses may arrive after stream removal events; never readd them to trays.
  let settledQueuedIds = new Set<string>();

  const withoutQueued = (messages: QueuedMessage[], id: string): QueuedMessage[] => {
    settledQueuedIds.add(id);
    return messages.some((m) => m.id === id) ? messages.filter((m) => m.id !== id) : messages;
  };

  // base is get().entries before mutation, next is the new reference about to enter state
  // (always distinct: every mutation constructs new arrays). Self-healing must compare
  // base against previous projectorSource, never next: next is by definition new,
  // so comparing it classifies every valid call as changed, rebuilding
  // projectors and deep-copying all history, degenerating to O(n²) full replay, precisely what
  // incremental projection avoids. Only external whole-frame replacement bypassing actions (for example
  // useAssistantStore.setState(..., true)) makes base differ from previous
  // projectorSource, correctly triggering rebuilds.
  const projectEntries = (base: TimelineEntry[], next: TimelineEntry[]): Turn[] => {
    if (projectorSource !== base) {
      projector = createTimelineProjector();
    }
    const turns = projector.project(next);
    projectorSource = next;
    return turns;
  };

  const committedFor = (entries: TimelineEntry[]): Set<string> => {
    if (committedSource !== entries) {
      committedIds = collectCommittedMessageIds(entries);
      committedSource = entries;
    }
    return committedIds;
  };

  return {
    sessions: [],
    currentSessionId: null,
    sessionsLoading: false,
    entries: [],
    draft: null,
    draftRev: 0,
    turns: [],
    draftTurn: null,
    messagesLoading: false,
    historySeq: -1,
    queuedMessages: [],
    input: "",
    composerAppends: [],
    sending: false,
    interrupting: false,
    error: null,
    startupFailure: null,
    startupFailureOrigin: null,
    sessionStatus: null,
    sessionStatusDetail: null,
    pendingQuestion: null,
    answeringQuestion: false,
    skills: [],
    skillsLoading: false,
    editingTurnUuid: null,
    currentProject: null,
    isDraftSession: false,
    sessionResumeSignals: [],

    setSessions: (sessions) => set({ sessions }),
    setCurrentSessionId: (id) => set({ currentSessionId: id }),
    setSessionsLoading: (loading) => set({ sessionsLoading: loading }),

    setEntries: (entries) => {
      // Union rather than whole-frame replacement: slow cold-read responses can arrive after send responses/SSE entries;
      // full replacement erases newer entries, whereas seq unions are always safe for append-only logs.
      const prevEntries = get().entries;
      const merged = mergeEntriesBySeq(prevEntries, entries);
      const draft = get().draft;
      committedIds = collectCommittedMessageIds(merged);
      committedSource = merged;
      set({
        entries: merged,
        turns: projectEntries(prevEntries, merged),
        draftTurn: buildDraftTurn(draft, isDraftReplaced(draft, committedIds)),
      });
    },
    appendEntry: (entry) => {
      const { entries, draft } = get();
      const lastSeq = entries.length > 0 ? entries[entries.length - 1].seq : -1;
      if (entry.seq <= lastSeq) return;
      const next = [...entries, entry];
      const ids = committedFor(entries);
      if (entry.type === "assistant" && entry.message_id != null) ids.add(entry.message_id);
      committedSource = next;
      // Persisted authoritative entries precisely replace same-message_id drafts by identity.
      const draftReplaced =
        draft !== null &&
        entry.type === "assistant" &&
        entry.message_id != null &&
        entry.message_id === draft.message_id;
      const nextDraft = draftReplaced ? null : draft;
      // User entries are accepted queued messages; identities must not appear once each in trays/timelines.
      const queuedMessages =
        entry.type === "user" && entry.uuid ? withoutQueued(get().queuedMessages, entry.uuid) : get().queuedMessages;
      set({
        entries: next,
        draft: nextDraft,
        queuedMessages,
        turns: projectEntries(entries, next),
        draftTurn: buildDraftTurn(nextDraft, isDraftReplaced(nextDraft, ids)),
      });
    },
    setDraftSnapshot: (draft, rev) => {
      // tool_json is server-accumulated original partial JSON; append later input_json_delta to this prefix,
      // or suffix-only parsing always fails and parameter previews freeze.
      const mirror: DraftMirror | null = draft
        ? { ...draft, content: [...(draft.content ?? [])], toolJson: { ...(draft.tool_json ?? {}) } }
        : null;
      set({
        draft: mirror,
        draftRev: rev,
        draftTurn: buildDraftTurn(mirror, isDraftReplaced(mirror, committedFor(get().entries))),
      });
    },
    applyDelta: (payload) => {
      const { draft, draftRev } = get();
      if (typeof payload.rev !== "number" || payload.rev <= draftRev) return;
      const next = applyDraftDelta(draft, payload);
      set({
        draft: next,
        draftRev: payload.rev,
        draftTurn: buildDraftTurn(next, isDraftReplaced(next, committedFor(get().entries))),
      });
    },
    clearDraft: () => set({ draft: null, draftTurn: null }),
    resetTimeline: () => {
      projector = createTimelineProjector();
      projectorSource = null;
      committedIds = new Set<string>();
      committedSource = null;
      settledQueuedIds = new Set<string>();
      set({
        entries: [],
        queuedMessages: [],
        draft: null,
        draftRev: 0,
        turns: [],
        draftTurn: null,
        historySeq: -1,
        startupFailure: null,
        startupFailureOrigin: null,
        interrupting: false,
        // Edit-state anchors belong to cleared timelines and no longer exist after rebuilding.
        editingTurnUuid: null,
      });
    },

    setMessagesLoading: (loading) => set({ messagesLoading: loading }),
    beginHistory: () => set({ historySeq: null }),
    settleHistory: () => {
      const { historySeq, entries } = get();
      if (historySeq === null) set({ historySeq: entries.at(-1)?.seq ?? -1 });
    },
    setQueuedMessages: (messages) => set({ queuedMessages: messages }),
    upsertQueuedMessage: (message) => {
      const { queuedMessages, entries } = get();
      if (settledQueuedIds.has(message.id) || entries.some((e) => e.uuid === message.id)) return;
      const index = queuedMessages.findIndex((m) => m.id === message.id);
      set({
        queuedMessages:
          index < 0
            ? [...queuedMessages, message]
            : queuedMessages.map((m, i) => (i === index ? message : m)),
      });
    },
    removeQueuedMessage: (id) => {
      const queuedMessages = withoutQueued(get().queuedMessages, id);
      if (queuedMessages !== get().queuedMessages) set({ queuedMessages });
    },
    hasLeftQueue: (id) => settledQueuedIds.has(id),
    setInput: (input) => set({ input }),
    appendToComposer: (content) => set({ composerAppends: [...get().composerAppends, content] }),
    takeComposerAppends: () => {
      const appends = get().composerAppends;
      if (appends.length > 0) set({ composerAppends: [] });
      return appends;
    },
    setSending: (sending) => set({ sending }),
    setInterrupting: (interrupting) => set({ interrupting }),
    setError: (error) => set({ error }),
    // Write origin/failure together, preventing independent drift.
    setStartupFailure: (failure, origin = "send") =>
      set({ startupFailure: failure, startupFailureOrigin: failure ? origin : null }),
    setSessionStatus: (status) => set({ sessionStatus: status }),
    setSessionStatusDetail: (detail) => set({ sessionStatusDetail: detail }),
    setPendingQuestion: (question) => set({ pendingQuestion: question }),
    setAnsweringQuestion: (answering) => set({ answeringQuestion: answering }),
    setSkills: (skills) => set({ skills }),
    setSkillsLoading: (loading) => set({ skillsLoading: loading }),
    setEditingTurnUuid: (uuid) => set({ editingTurnUuid: uuid }),
    setCurrentProject: (project) => set({ currentProject: project }),
    setIsDraftSession: (draft) => set({ isDraftSession: draft }),
    notifySessionResumed: (projectName, sessionId) =>
      set((s) => ({ sessionResumeSignals: [...s.sessionResumeSignals, { kind: "resumed", projectName, sessionId }] })),
    requestSessionResync: (projectName) =>
      set((s) => ({ sessionResumeSignals: [...s.sessionResumeSignals, { kind: "resync", projectName }] })),
    takeSessionResumeSignals: () => {
      const signals = get().sessionResumeSignals;
      if (signals.length > 0) set({ sessionResumeSignals: [] });
      return signals;
    },
  };
});
