// ---------------------------------------------------------------------------
// PROTOTYPE — THROWAWAY (#1692 / Spec #974)。不合入 main，仅存于原型分支。
//
// 计划：三个结构不同的「消息改写」交互变体，挂在既有助手面板消息列表上，
// `?msgedit=A|B|C` 切换，仅 DEV 构建启用。四个关键态：hover 入口、编辑态、
// 禁用态（未决问答）、后果说明。「重新发送」是桩——不触发任何真实请求。
//
//   A 右下操作行 · 气泡内编辑   气泡下方右对齐操作行（时间 · 复制 · 编辑，Codex 式），编辑在气泡内完成
//   B 操作条 · 全宽改写卡       入口带文字：气泡下缘操作条，编辑态是占满行宽的卡片
//   C 截断预览 · 后果可视化     编辑时其后消息立即变暗，后果由文字 + 视觉双重表达
//
// HITL 结论（用户已选 A 并逐轮修正，已落在下方实现）：
//   1. 编辑入口用 Codex 式操作行：气泡下方右对齐「时间 · 复制 · 编辑」，hover 浮现，
//      行高常驻避免高度跳动；按钮本体无底色，hover 出灰色圆角方形背景
//   2. 不可编辑（智能体运行中 sessionStatus === "running"，或有未决问答）时
//      编辑按钮直接不显示，仅保留复制按钮
//
// 抛弃式豁免：硬编码中文（不建 i18n key）、无测试、状态直接放模块级 store。
// ---------------------------------------------------------------------------
import { useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { Check, Copy, Lock, Pencil, Scissors, TriangleAlert } from "lucide-react";
import type { Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { ChatMessage } from "../chat/ChatMessage";

// vitest 下 DEV 同为 true，排除 TEST 以免既有测试踩进原型渲染分支
export const MSG_EDIT_PROTO_ENABLED = import.meta.env.DEV && !import.meta.env.TEST;

const VARIANTS = ["A", "B", "C"] as const;
type Variant = (typeof VARIANTS)[number];

const VARIANT_NAMES: Record<Variant, string> = {
  A: "右下操作行 · 气泡内编辑",
  B: "操作条 · 全宽改写卡",
  C: "截断预览 · 后果可视化",
};

// Spec #974 固定文案
const CONSEQUENCE = "此消息之后的对话将被丢弃，已产生的文件修改不会撤销";
const BLOCKED_REASON_QA = "请先完成问答卡片，再编辑历史消息";
const BLOCKED_REASON_RUNNING = "智能体正在运行，完成或中断后可编辑历史消息";
const FLASH_TEXT =
  "原型桩：实际实现将在此创建分支会话，并以改写后的消息从这里重跑（智能体运行中会先中断）";

// 警示琥珀色——区别于既有错误红（oklch 0.70 0.18 25），表达「后果提示」而非「错误」
const AMBER = "oklch(0.80 0.12 80)";
const AMBER_DIM = "oklch(0.80 0.12 80 / 0.12)";
const AMBER_BORDER = "oklch(0.80 0.12 80 / 0.35)";

// ---------------------------------------------------------------------------
// 原型状态（模块级 zustand，抛弃式）
// ---------------------------------------------------------------------------

// 变体读初值自 ?msgedit=，之后由 store 驱动；写回 URL 用 history.replaceState——
// 助手面板在 <Route nest> 内，wouter 的 navigate 会把 base 前缀拼两次
const initialVariant = (): Variant => {
  const raw = new URLSearchParams(window.location.search).get("msgedit");
  return raw === "B" || raw === "C" ? raw : "A";
};

interface ProtoState {
  variant: Variant;
  editingId: string | null;
  draft: string;
  simulateQA: boolean;
  simulateRunning: boolean;
  flash: string | null;
  setVariant: (v: Variant) => void;
  startEdit: (id: string, text: string) => void;
  setDraft: (text: string) => void;
  cancelEdit: () => void;
  resend: () => void;
  toggleQA: () => void;
  toggleRunning: () => void;
}

const useProtoStore = create<ProtoState>((set) => ({
  variant: initialVariant(),
  editingId: null,
  draft: "",
  simulateQA: false,
  simulateRunning: false,
  flash: null,
  setVariant: (v) => {
    const params = new URLSearchParams(window.location.search);
    params.set("msgedit", v);
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
    set({ variant: v, editingId: null, draft: "" });
  },
  startEdit: (id, text) => set({ editingId: id, draft: text }),
  setDraft: (text) => set({ draft: text }),
  cancelEdit: () => set({ editingId: null, draft: "" }),
  resend: () => {
    set({ editingId: null, draft: "", flash: FLASH_TEXT });
    setTimeout(() => set({ flash: null }), 5000);
  },
  toggleQA: () => set((s) => ({ simulateQA: !s.simulateQA })),
  toggleRunning: () => set((s) => ({ simulateRunning: !s.simulateRunning })),
}));

// ---------------------------------------------------------------------------
// 演示消息——当前会话没有历史用户消息时垫底，保证四个态都能操作。
// 场景取自 Spec 动机：要求逐集修改，agent 滑向批量写脚本。
// ---------------------------------------------------------------------------

const mockTurn = (i: number, type: Turn["type"], text: string): Turn => ({
  type,
  uuid: `proto-mock-${i}`,
  timestamp: `2026-05-02T14:2${i}:00`,
  content: [{ type: "text", text }],
});

const MOCK_TURNS: Turn[] = [
  mockTurn(1, "user", "帮我把第 3 集的剧本里「林昭」的台词改得更口语一些，只改第 3 集。"),
  mockTurn(
    2,
    "assistant",
    "好的。我注意到第 4、5 集也有同样书面化的台词，我先写一个批量脚本统一处理所有剧集，这样更高效。",
  ),
  mockTurn(3, "user", "不要批量处理，我说了只改第 3 集。"),
  mockTurn(
    4,
    "assistant",
    "明白。不过批量脚本已经写好了，我先跑一遍 dry-run 看看会改动哪些文件，再决定是否只应用第 3 集的部分。",
  ),
  mockTurn(5, "user", "停下，回到第 3 集，逐条给我看你要改哪些台词。"),
];

const turnText = (turn: Turn): string =>
  (turn.content ?? [])
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text)
    .join("\n\n");

const isEditableUserTurn = (turn: Turn): boolean =>
  turn.type === "user" && turn.subtype !== "question_answer" && turnText(turn).trim().length > 0;

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

// Codex 式无底色图标按钮：hover 出灰色圆角方形背景；点击后短暂显示已复制
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  const Icon = copied ? Check : Copy;
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
      }}
      title="复制消息内容"
      aria-label="复制消息内容"
      className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10"
      style={{ color: copied ? "var(--color-accent-2)" : "var(--color-text-3)" }}
    >
      <Icon aria-hidden className="h-3.5 w-3.5" />
    </button>
  );
}

// ---------------------------------------------------------------------------
// 消息列表（替换 AgentCopilot 内原本的 allTurns.map）
// ---------------------------------------------------------------------------

export function MsgEditProtoList({ allTurns, draftTurn }: { allTurns: Turn[]; draftTurn: Turn | null }) {
  const pendingQuestion = useAssistantStore((s) => s.pendingQuestion);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const { variant, simulateQA, simulateRunning, editingId, flash } = useProtoStore();

  const usingMocks = !allTurns.some((t) => t.type === "user");
  const turns = usingMocks ? MOCK_TURNS : allTurns;
  // 未决问答与运行中同为禁用；问答原因更可行动，优先展示
  const qaBlocked = Boolean(pendingQuestion) || simulateQA;
  const runningBlocked = sessionStatus === "running" || simulateRunning;
  const blocked = qaBlocked || runningBlocked;
  const blockedReason = qaBlocked ? BLOCKED_REASON_QA : BLOCKED_REASON_RUNNING;

  const turnId = (turn: Turn, i: number) => turn.uuid || `proto-idx-${i}`;
  const editingIndex = editingId === null ? -1 : turns.findIndex((t, i) => turnId(t, i) === editingId);
  const droppedCount = editingIndex >= 0 ? turns.length - editingIndex - 1 : 0;

  return (
    <>
      {usingMocks && (
        <p className="text-center text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
          原型演示消息——当前会话没有历史用户消息
        </p>
      )}
      {turns.map((turn, i) => {
        const id = turnId(turn, i);
        // 变体 C：编辑点之后的消息变暗，预演截断后果
        const dimmed = variant === "C" && editingIndex >= 0 && i > editingIndex;
        const node = isEditableUserTurn(turn) ? (
          <EditableUserMessage
            turn={turn}
            id={id}
            variant={variant}
            blocked={blocked}
            blockedReason={blockedReason}
            droppedCount={droppedCount}
          />
        ) : variant === "A" && turn.type === "assistant" && turn !== draftTurn && turnText(turn).trim() ? (
          // A：助手消息下方留同高操作行（时间 · 复制），消息节奏与用户消息一致
          <div className="group">
            <ChatMessage message={turn} />
            <div className="flex h-7 items-center gap-0.5 pl-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
              <CopyButton text={turnText(turn)} />
              {turn.timestamp && (
                <span className="ml-1 text-[10.5px] tabular-nums" style={{ color: "var(--color-text-4)" }}>
                  {formatTime(turn.timestamp)}
                </span>
              )}
            </div>
          </div>
        ) : (
          <ChatMessage message={turn} streaming={turn === draftTurn} />
        );
        return (
          <div
            key={id}
            aria-hidden={dimmed || undefined}
            className="transition-[opacity,filter] duration-300"
            style={dimmed ? { opacity: 0.3, filter: "grayscale(0.9)", pointerEvents: "none", userSelect: "none" } : undefined}
          >
            {node}
          </div>
        );
      })}
      {simulateQA && <MockQuestionCard />}
      {flash && (
        <div
          role="status"
          className="fixed bottom-16 left-1/2 z-50 w-[420px] max-w-[90vw] -translate-x-1/2 rounded-lg px-3 py-2 text-[11.5px] shadow-lg"
          style={{
            background: "oklch(0.24 0.012 265)",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-text)",
          }}
        >
          {flash}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 单条可编辑用户消息——按变体分发入口与编辑态
// ---------------------------------------------------------------------------

interface EditableProps {
  turn: Turn;
  id: string;
  variant: Variant;
  blocked: boolean;
  blockedReason: string;
  /** 变体 C 横幅用：编辑点之后将被丢弃的消息数 */
  droppedCount: number;
}

function EditableUserMessage({ turn, id, variant, blocked, blockedReason, droppedCount }: EditableProps) {
  const { editingId, startEdit, cancelEdit } = useProtoStore();
  const editing = editingId === id;
  const [blockedTip, setBlockedTip] = useState(false);

  useEffect(() => {
    if (!blockedTip) return;
    const timer = setTimeout(() => setBlockedTip(false), 3000);
    return () => clearTimeout(timer);
  }, [blockedTip]);

  const onEntryClick = () => {
    if (blocked) {
      setBlockedTip(true);
      return;
    }
    startEdit(id, turnText(turn));
  };

  if (editing) {
    return variant === "B" ? (
      <CardEditor onCancel={cancelEdit} />
    ) : (
      <BubbleEditor showInlineNote={variant !== "C"} truncationBanner={variant === "C" ? droppedCount : null} onCancel={cancelEdit} />
    );
  }

  // --- 非编辑态：气泡 + 按变体的 hover 入口 ---

  if (variant === "B") {
    // B：气泡下缘浮现操作条（带文字），禁用原因直接写在条内，不用 tooltip
    return (
      <div className="group relative pb-1">
        <ChatMessage message={turn} />
        <div
          className="pointer-events-none absolute -bottom-2 right-1 z-10 flex items-center gap-2 opacity-0 transition-opacity duration-150 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
        >
          {blocked && (
            <span className="rounded px-1.5 py-0.5 text-[10.5px]" style={{ background: "oklch(0.22 0.011 265)", color: "var(--color-text-3)", border: "1px solid var(--color-hairline-soft)" }}>
              {blockedReason}
            </span>
          )}
          <button
            type="button"
            disabled={blocked}
            onClick={onEntryClick}
            className="focus-ring flex items-center gap-1 rounded-md px-2 py-1 text-[11px] shadow-md transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: "oklch(0.26 0.014 270)",
              border: "1px solid var(--color-hairline-soft)",
              color: blocked ? "var(--color-text-4)" : "var(--color-text-2)",
            }}
          >
            <Pencil aria-hidden className="h-3 w-3" />
            编辑并重发
          </button>
        </div>
      </div>
    );
  }

  if (variant === "A") {
    // A（Codex 式）：气泡下方右对齐操作行「时间 · 复制 · 编辑」，行高常驻（hover 只改
    // 透明度，无高度跳动）；不可编辑时编辑按钮不渲染，仅保留复制
    return (
      <div className="group">
        <ChatMessage message={turn} />
        <div className="flex h-7 items-center justify-end gap-0.5 pr-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
          {turn.timestamp && (
            <span className="mr-1 text-[10.5px] tabular-nums" style={{ color: "var(--color-text-4)" }}>
              {formatTime(turn.timestamp)}
            </span>
          )}
          <CopyButton text={turnText(turn)} />
          {!blocked && (
            <button
              type="button"
              onClick={onEntryClick}
              title="编辑此消息并从这里重新发送"
              aria-label="编辑此消息并从这里重新发送"
              className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10"
              style={{ color: "var(--color-text-3)" }}
            >
              <Pencil aria-hidden className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  // C：气泡左侧浮现小圆图标（禁用时换锁形，点击显示原因）
  const Icon = blocked ? Lock : Pencil;
  return (
    <div className="group relative flex items-center justify-end gap-1.5">
      {blockedTip && (
        <span className="rounded px-1.5 py-0.5 text-[10.5px]" style={{ background: "oklch(0.22 0.011 265)", color: "var(--color-text-3)", border: "1px solid var(--color-hairline-soft)" }}>
          {blockedReason}
        </span>
      )}
      <button
        type="button"
        aria-disabled={blocked}
        onClick={onEntryClick}
        title={blocked ? blockedReason : "编辑此消息并从这里重新发送"}
        aria-label={blocked ? blockedReason : "编辑此消息并从这里重新发送"}
        className="focus-ring grid h-6 w-6 shrink-0 place-items-center rounded-full opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
        style={{
          background: "oklch(0.24 0.012 265)",
          border: "1px solid var(--color-hairline-soft)",
          color: blocked ? "var(--color-text-4)" : "var(--color-text-2)",
        }}
      >
        <Icon aria-hidden className="h-3 w-3" />
      </button>
      <ChatMessage message={turn} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 编辑态：气泡内联编辑器（变体 A / C）
// ---------------------------------------------------------------------------

function BubbleEditor({
  showInlineNote,
  truncationBanner,
  onCancel,
}: {
  showInlineNote: boolean;
  /** 变体 C：非 null 时在编辑框下方渲染截断横幅，数值为将被丢弃的消息数 */
  truncationBanner: number | null;
  onCancel: () => void;
}) {
  return (
    <div>
      <div
        className="ml-auto max-w-[85%] rounded-xl px-2.5 py-1.5"
        style={{
          background: "linear-gradient(180deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.06))",
          border: "1px solid var(--color-accent-soft)",
        }}
      >
        <div className="mb-1 text-[10px] font-semibold uppercase" style={{ color: "var(--color-accent-2)", letterSpacing: "0.06em" }}>
          编辑中
        </div>
        <EditorTextarea />
        {showInlineNote && (
          <p className="mt-1.5 flex items-start gap-1 text-[10.5px] leading-[1.5]" style={{ color: AMBER }}>
            <TriangleAlert aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
            {CONSEQUENCE}
          </p>
        )}
        <EditorButtons onCancel={onCancel} />
      </div>
      {truncationBanner !== null && (
        <div
          className="mt-2 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[11px]"
          style={{ background: AMBER_DIM, border: `1px dashed ${AMBER_BORDER}`, color: AMBER }}
        >
          <Scissors aria-hidden className="h-3.5 w-3.5 shrink-0" />
          <span>
            以下 {truncationBanner} 条消息将被丢弃 · 已产生的文件修改不会撤销
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 编辑态：全宽改写卡（变体 B）
// ---------------------------------------------------------------------------

function CardEditor({ onCancel }: { onCancel: () => void }) {
  return (
    <div
      className="rounded-xl p-3"
      style={{ background: "oklch(0.22 0.012 268)", border: "1px solid var(--color-accent-soft)" }}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[10px] font-semibold uppercase" style={{ color: "var(--color-accent-2)", letterSpacing: "0.08em" }}>
          改写此消息
        </span>
        <span className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
          将从这里开启新的对话分支
        </span>
      </div>
      <EditorTextarea />
      <div
        className="mt-2 flex items-start gap-1.5 rounded-md px-2 py-1.5 text-[11px] leading-[1.5]"
        style={{ background: AMBER_DIM, border: `1px solid ${AMBER_BORDER}`, color: AMBER }}
      >
        <TriangleAlert aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {CONSEQUENCE}
      </div>
      <EditorButtons onCancel={onCancel} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 编辑器共用件
// ---------------------------------------------------------------------------

function EditorTextarea() {
  const { draft, setDraft, resend, cancelEdit } = useProtoStore();
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  return (
    <textarea
      ref={ref}
      value={draft}
      aria-label="改写消息内容"
      onChange={(e) => {
        setDraft(e.target.value);
        e.currentTarget.style.height = "auto";
        e.currentTarget.style.height = `${e.currentTarget.scrollHeight}px`;
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          cancelEdit();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          resend();
        }
      }}
      rows={2}
      className="w-full resize-none rounded-md px-2 py-1.5 text-[12.5px] leading-[1.55] outline-none"
      style={{
        background: "oklch(0.17 0.01 260 / 0.6)",
        border: "1px solid var(--color-hairline-soft)",
        color: "var(--color-text)",
      }}
    />
  );
}

function EditorButtons({ onCancel }: { onCancel: () => void }) {
  const { draft, resend } = useProtoStore();
  const empty = draft.trim().length === 0;
  return (
    <div className="mt-2 flex items-center justify-end gap-2">
      <button
        type="button"
        onClick={onCancel}
        className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] transition-colors"
        style={{ color: "var(--color-text-3)", border: "1px solid var(--color-hairline-soft)" }}
      >
        取消
      </button>
      <button
        type="button"
        disabled={empty}
        onClick={resend}
        title="⌘/Ctrl + Enter"
        className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ background: "var(--color-accent)", color: "oklch(0.12 0 0)" }}
      >
        重新发送
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模拟未决问答卡片（仅原型开关驱动，无真实交互）
// ---------------------------------------------------------------------------

function MockQuestionCard() {
  return (
    <div
      className="rounded-xl px-3 py-2.5"
      style={{ background: "oklch(0.22 0.011 265 / 0.7)", border: "1px solid var(--color-accent-soft)" }}
    >
      <div className="mb-1.5 text-[10px] font-semibold uppercase" style={{ color: "var(--color-accent-2)", letterSpacing: "0.06em" }}>
        智能体的提问（原型模拟）
      </div>
      <p className="mb-2 text-[12.5px]" style={{ color: "var(--color-text)" }}>
        第 3 集台词的口语化程度，你希望保留多少书面语？
      </p>
      <div className="flex flex-wrap gap-1.5">
        {["完全口语化", "保留部分书面语"].map((label) => (
          <button
            key={label}
            type="button"
            disabled
            className="cursor-not-allowed rounded-md px-2 py-1 text-[11.5px] opacity-70"
            style={{ border: "1px solid var(--color-hairline-soft)", color: "var(--color-text-2)" }}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
        由底部原型开关模拟，用于查看编辑入口的禁用态
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 悬浮切换条（UI 原型规约件：←/→ 循环变体 + 模拟开关；刻意做成与产品视觉无关的高对比样式）
// ---------------------------------------------------------------------------

export function MsgEditProtoSwitcher() {
  const { variant, setVariant, simulateQA, toggleQA, simulateRunning, toggleRunning } = useProtoStore();

  const cycle = (dir: 1 | -1) => {
    setVariant(VARIANTS[(VARIANTS.indexOf(variant) + dir + VARIANTS.length) % VARIANTS.length]);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.closest("input, textarea, [contenteditable]") || e.isComposing)) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div
      className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full py-1.5 pl-2 pr-3 text-[12px] shadow-xl"
      style={{ background: "oklch(0.97 0.01 100)", color: "oklch(0.2 0 0)", border: "2px solid oklch(0.75 0.15 85)" }}
    >
      <button type="button" onClick={() => cycle(-1)} aria-label="上一个变体" className="rounded-full px-1.5 font-bold hover:bg-black/10">
        ←
      </button>
      <span className="whitespace-nowrap font-medium">
        原型 {variant} — {VARIANT_NAMES[variant]}
      </span>
      <button type="button" onClick={() => cycle(1)} aria-label="下一个变体" className="rounded-full px-1.5 font-bold hover:bg-black/10">
        →
      </button>
      <span aria-hidden className="h-4 w-px bg-black/20" />
      <label className="flex cursor-pointer items-center gap-1 whitespace-nowrap">
        <input type="checkbox" checked={simulateQA} onChange={toggleQA} className="accent-amber-600" />
        模拟未决问答
      </label>
      <label className="flex cursor-pointer items-center gap-1 whitespace-nowrap">
        <input type="checkbox" checked={simulateRunning} onChange={toggleRunning} className="accent-amber-600" />
        模拟运行中
      </label>
    </div>
  );
}
