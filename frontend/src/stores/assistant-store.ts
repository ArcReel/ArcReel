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
  buildDraftTurn,
  collectCommittedMessageIds,
  createTimelineProjector,
  isDraftReplaced,
  mergeEntriesBySeq,
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

export const useAssistantStore = create<AssistantState>((set, get) => {
  // 投影派生缓存（非响应式状态）：projector 增量折叠 entries→turns，
  // committedIds 是 draft 替换判定的 O(1) 索引。两者都按 entries 引用自愈——
  // 外部整帧 setState（如测试 reset）替换 entries 却不经本 store 的 action
  // 时，下次读取按引用不符重建，避免用陈旧 message_id 误判 draft 已替换，
  // 或用陈旧 projector 内部状态误判增量前缀延续。projector 自身的前缀判定
  // 只比对 entries 首尾元素引用（O(1)，不能整数组比对，否则每次追加都会
  // 判定为"变了"而失去增量的意义），这里额外按容器引用做一层更粗但更可靠
  // 的自愈防线——与 committedSource 完全同构，legitimate 的每次 mutation
  // 都把 xxxSource 同步更新为下一次 get().entries 会拿到的引用，只有外部
  // 绕过 action 的整帧替换才会触发。
  let projector = createTimelineProjector();
  let projectorSource: TimelineEntry[] | null = null;
  let committedIds = new Set<string>();
  let committedSource: TimelineEntry[] | null = null;

  // base 是本次 mutation 之前 get().entries 持有的引用，next 是即将写入 state
  // 的新引用（二者恒不相等——每次 mutation 都会构造新数组）。自愈检查必须
  // 拿 base 去比对上一次记录的 projectorSource，而不是拿 next 比对：next
  // 由定义就是全新引用，若拿它做比对，每次合法调用都会判定"变了"从而重建
  // projector、对全部历史条目重新深拷贝，退化为 O(n²) 全量重放——这正是
  // 增量投影要消除的问题。只有外部绕过 action 的整帧替换（如测试
  // useAssistantStore.setState(..., true)）才会让 base 与上次记录的
  // projectorSource 不一致，进而正确触发重建。
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
        turns: projectEntries(entries, next),
        draftTurn: buildDraftTurn(nextDraft, isDraftReplaced(nextDraft, ids)),
      });
    },
    setDraftSnapshot: (draft, rev) => {
      // tool_json 是服务端已累积的原始 partial JSON——以此为前缀继续拼接
      // 后续 input_json_delta，否则纯后缀永远解析失败、参数预览冻结。
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
      set({ entries: [], draft: null, draftRev: 0, turns: [], draftTurn: null });
    },

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
  };
});
