import { create } from "zustand";
import type {
  DraftDeltaPayload,
  DraftState,
  PendingQuestion,
  SessionMeta,
  SessionStatus,
  SkillInfo,
  TimelineEntry,
  Turn,
} from "@/types";
import {
  applyDraftDelta,
  mergeEntriesBySeq,
  projectDraftToTurn,
  projectEntriesToTurns,
  type DraftMirror,
} from "@/utils/entry-projection";

interface AssistantState {
  // Sessions
  sessions: SessionMeta[];
  currentSessionId: string | null;
  sessionsLoading: boolean;

  // Timeline（事件日志唯一读源）
  entries: TimelineEntry[];
  draft: DraftMirror | null;
  draftRev: number;
  // 由 entries/draft 投影派生，仅 timeline actions 写入
  turns: Turn[];
  draftTurn: Turn | null;
  messagesLoading: boolean;

  // Input
  input: string;
  sending: boolean;
  interrupting: boolean;
  error: string | null;

  // Session status
  sessionStatus: SessionStatus | null;
  sessionStatusDetail: string | null;

  // Questions
  pendingQuestion: PendingQuestion | null;
  answeringQuestion: boolean;

  // Skills
  skills: SkillInfo[];
  skillsLoading: boolean;

  // Scope
  currentProject: string | null;

  // Draft session (lazy creation)
  isDraftSession: boolean;

  // Actions
  setSessions: (sessions: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setSessionsLoading: (loading: boolean) => void;
  /** 整帧替换日志条目（冷读 / 切换会话）。 */
  setEntries: (entries: TimelineEntry[]) => void;
  /** 追加单条权威条目；seq 门槛去重（身份比对，非内容比对）。 */
  appendEntry: (entry: TimelineEntry) => void;
  /** draft 首帧快照（重连携带累积态 + rev 过滤门槛）。 */
  setDraftSnapshot: (draft: DraftState | null, rev: number) => void;
  /** 应用一条流式 delta；rev 未超过门槛时忽略。 */
  applyDelta: (payload: DraftDeltaPayload) => void;
  clearDraft: () => void;
  /** 清空时间线（项目切换 / 新会话）。 */
  resetTimeline: () => void;
  setMessagesLoading: (loading: boolean) => void;
  setInput: (input: string) => void;
  setSending: (sending: boolean) => void;
  setInterrupting: (interrupting: boolean) => void;
  setError: (error: string | null) => void;
  setSessionStatus: (status: SessionStatus | null) => void;
  setSessionStatusDetail: (detail: string | null) => void;
  setPendingQuestion: (question: PendingQuestion | null) => void;
  setAnsweringQuestion: (answering: boolean) => void;
  setSkills: (skills: SkillInfo[]) => void;
  setSkillsLoading: (loading: boolean) => void;
  setCurrentProject: (project: string | null) => void;
  setIsDraftSession: (draft: boolean) => void;
}

export const useAssistantStore = create<AssistantState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  sessionsLoading: false,
  entries: [],
  draft: null,
  draftRev: 0,
  turns: [],
  draftTurn: null,
  messagesLoading: false,
  input: "",
  sending: false,
  interrupting: false,
  error: null,
  sessionStatus: null,
  sessionStatusDetail: null,
  pendingQuestion: null,
  answeringQuestion: false,
  skills: [],
  skillsLoading: false,
  currentProject: null,
  isDraftSession: false,

  setSessions: (sessions) => set({ sessions }),
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setSessionsLoading: (loading) => set({ sessionsLoading: loading }),

  setEntries: (entries) => {
    // 并集合并而非整帧覆盖：慢网络下冷读响应可能晚于发送响应/SSE 条目到达，
    // 整帧覆盖会抹掉更新的条目；append-only 日志按 seq 并集恒安全。
    const merged = mergeEntriesBySeq(get().entries, entries);
    const draft = get().draft;
    set({
      entries: merged,
      turns: projectEntriesToTurns(merged),
      draftTurn: projectDraftToTurn(draft, merged),
    });
  },
  appendEntry: (entry) => {
    const { entries, draft } = get();
    const lastSeq = entries.length > 0 ? entries[entries.length - 1].seq : -1;
    if (entry.seq <= lastSeq) return;
    const next = [...entries, entry];
    // 权威条目落库即按同 message_id 精确替换 draft（身份比对）
    const draftReplaced =
      draft !== null &&
      entry.type === "assistant" &&
      entry.message_id != null &&
      entry.message_id === draft.message_id;
    const nextDraft = draftReplaced ? null : draft;
    set({
      entries: next,
      draft: nextDraft,
      turns: projectEntriesToTurns(next),
      draftTurn: projectDraftToTurn(nextDraft, next),
    });
  },
  setDraftSnapshot: (draft, rev) => {
    const entries = get().entries;
    // tool_json 是服务端已累积的原始 partial JSON——以此为前缀继续拼接
    // 后续 input_json_delta，否则纯后缀永远解析失败、参数预览冻结。
    const mirror: DraftMirror | null = draft
      ? { ...draft, content: [...draft.content], toolJson: { ...(draft.tool_json ?? {}) } }
      : null;
    set({
      draft: mirror,
      draftRev: rev,
      draftTurn: projectDraftToTurn(mirror, entries),
    });
  },
  applyDelta: (payload) => {
    const { draft, draftRev, entries } = get();
    if (typeof payload.rev !== "number" || payload.rev <= draftRev) return;
    const next = applyDraftDelta(draft, payload);
    set({
      draft: next,
      draftRev: payload.rev,
      draftTurn: projectDraftToTurn(next, entries),
    });
  },
  clearDraft: () => set({ draft: null, draftTurn: null }),
  resetTimeline: () =>
    set({ entries: [], draft: null, draftRev: 0, turns: [], draftTurn: null }),

  setMessagesLoading: (loading) => set({ messagesLoading: loading }),
  setInput: (input) => set({ input }),
  setSending: (sending) => set({ sending }),
  setInterrupting: (interrupting) => set({ interrupting }),
  setError: (error) => set({ error }),
  setSessionStatus: (status) => set({ sessionStatus: status }),
  setSessionStatusDetail: (detail) => set({ sessionStatusDetail: detail }),
  setPendingQuestion: (question) => set({ pendingQuestion: question }),
  setAnsweringQuestion: (answering) => set({ answeringQuestion: answering }),
  setSkills: (skills) => set({ skills }),
  setSkillsLoading: (loading) => set({ skillsLoading: loading }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setIsDraftSession: (draft) => set({ isDraftSession: draft }),
}));
