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

export type StartupFailureOrigin = "send" | "rewrite";

export type SessionResumeSignal =
  | { kind: "resumed"; projectName: string; sessionId: string }
  | { kind: "resync"; projectName: string };

interface AssistantState {
  sessions: SessionMeta[];
  currentSessionId: string | null;
  sessionsLoading: boolean;

  entries: TimelineEntry[];
  draft: DraftMirror | null;
  draftRev: number;
  turns: Turn[];
  draftTurn: Turn | null;
  messagesLoading: boolean;
  historySeq: number | null;
  queuedMessages: QueuedMessage[];

  input: string;
  composerAppends: ContentBlock[][];
  sending: boolean;
  interrupting: boolean;
  error: string | null;
  startupFailure: FailureObservation | null;
  startupFailureOrigin: StartupFailureOrigin | null;

  sessionStatus: SessionStatus | null;
  sessionStatusDetail: string | null;

  pendingQuestion: PendingQuestion | null;
  answeringQuestion: boolean;

  skills: SkillInfo[];
  skillsLoading: boolean;

  editingTurnUuid: string | null;

  currentProject: string | null;

  isDraftSession: boolean;

  sessionResumeSignals: SessionResumeSignal[];

  setSessions: (sessions: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setSessionsLoading: (loading: boolean) => void;
  setEntries: (entries: TimelineEntry[]) => void;
  appendEntry: (entry: TimelineEntry) => void;
  setDraftSnapshot: (draft: DraftState | null, rev: number) => void;
  applyDelta: (payload: DraftDeltaPayload) => void;
  clearDraft: () => void;
  resetTimeline: () => void;
  setMessagesLoading: (loading: boolean) => void;
  beginHistory: () => void;
  settleHistory: () => void;
  setQueuedMessages: (messages: QueuedMessage[]) => void;
  upsertQueuedMessage: (message: QueuedMessage) => void;
  removeQueuedMessage: (id: string) => void;
  hasLeftQueue: (id: string) => boolean;
  setInput: (input: string) => void;
  appendToComposer: (content: ContentBlock[]) => void;
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
  takeSessionResumeSignals: () => SessionResumeSignal[];
}

export const useAssistantStore = create<AssistantState>((set, get) => {
  let projector = createTimelineProjector();
  let projectorSource: TimelineEntry[] | null = null;
  let committedIds = new Set<string>();
  let committedSource: TimelineEntry[] | null = null;
  let settledQueuedIds = new Set<string>();

  const withoutQueued = (messages: QueuedMessage[], id: string): QueuedMessage[] => {
    settledQueuedIds.add(id);
    return messages.some((m) => m.id === id) ? messages.filter((m) => m.id !== id) : messages;
  };

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
      const draftReplaced =
        draft !== null &&
        entry.type === "assistant" &&
        entry.message_id != null &&
        entry.message_id === draft.message_id;
      const nextDraft = draftReplaced ? null : draft;
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
