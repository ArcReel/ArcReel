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

/** 启动失败的来源入口——决定故障卡片的重试重放哪一处输入。 */
export type StartupFailureOrigin = "send" | "rewrite";

export type SessionResumeSignal =
  | { kind: "resumed"; projectName: string; sessionId: string }
  | { kind: "resync"; projectName: string };

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
  /**
   * 载入会话时已有的最后一条条目的 seq：不大于它的条目是打开会话前的历史，大于它的是
   * 查看期间新到达的，失败卡片只为后者播报。null 表示历史仍在回放（running 会话经
   * entry 流补发存量），此时到达的条目都算历史。没有载入过程的会话（新建、草稿）为 -1。
   */
  historySeq: number | null;
  /** 排队消息托盘：已发出、Agent 尚未接纳的消息，按发送顺序排列；不进时间线。 */
  queuedMessages: QueuedMessage[];

  // Input
  input: string;
  /** 待追加到输入框末尾的消息内容（编辑排队消息时退回），由输入框取走。 */
  composerAppends: ContentBlock[][];
  sending: boolean;
  interrupting: boolean;
  error: string | null;
  /** 当前面板生命周期内最近一次 Agent 启动失败观测；不做跨刷新持久化。 */
  startupFailure: FailureObservation | null;
  /**
   * 该次启动失败由哪条入口产生。故障卡片的重试只重放仍保留原始输入的那一处：
   * `send` 的输入留在主输入框，`rewrite` 的留在仍开着的原地编辑器里。
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
   * 正处于原地编辑态的用户消息（条目 uuid）；同一时刻至多一条。
   * 收在 store 而非组件本地 state，才能让「至多一条」这条约束在整个时间线上成立。
   */
  editingTurnUuid: string | null;

  // Scope
  currentProject: string | null;

  // Draft session (lazy creation)
  isDraftSession: boolean;

  /**
   * 项目事件流送来、尚未消费的会话恢复信号，由会话 hook 整批取走。resumed：会话未经发送、
   * 自主回到 running；resync：事件流（重新）建连，此前的恢复通知可能已错过，需要核对。
   * 排队而非只留最新一条：同一批事件里的多条信号在 hook 消费前到达，后者会覆盖前者。
   */
  sessionResumeSignals: SessionResumeSignal[];

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
  /** 开始载入会话历史：此后到达的条目都算历史，直到 settleHistory。 */
  beginHistory: () => void;
  /** 会话历史载入完毕：以当前最后一条条目为界；已经定界时不变。 */
  settleHistory: () => void;
  /** 整体替换排队消息（entry 流开场快照）。 */
  setQueuedMessages: (messages: QueuedMessage[]) => void;
  /** 加入或更新一条排队消息；已离开排队的消息不再加回。 */
  upsertQueuedMessage: (message: QueuedMessage) => void;
  /** 移出一条排队消息（已被接纳或已被丢弃）。 */
  removeQueuedMessage: (id: string) => void;
  /** 这条消息是否已经离开排队（被接纳或被丢弃）。 */
  hasLeftQueue: (id: string) => boolean;
  setInput: (input: string) => void;
  /** 把一条消息的内容交给输入框，追加在已有内容之后。 */
  appendToComposer: (content: ContentBlock[]) => void;
  /** 输入框取走待追加的内容。 */
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
  /** 取走并清空待处理的恢复信号。 */
  takeSessionResumeSignals: () => SessionResumeSignal[];
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
  // 已离开排队的消息 id：发送响应可能晚于流上的移出事件到达，据此不把它加回托盘
  let settledQueuedIds = new Set<string>();

  const withoutQueued = (messages: QueuedMessage[], id: string): QueuedMessage[] => {
    settledQueuedIds.add(id);
    return messages.some((m) => m.id === id) ? messages.filter((m) => m.id !== id) : messages;
  };

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
      // 用户条目即被接纳的排队消息，同一身份不在托盘与时间线上各出现一次
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
        // 编辑态锚在被清空的那条时间线上，重建后锚点不再存在
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
    // origin 与 failure 同一次写入，两者不会各自漂移
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
