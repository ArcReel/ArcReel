import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Pencil, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Turn } from "@/types";
import { copyText } from "@/utils/clipboard";
import { formatClockTime } from "@/utils/date-format";
import { ChatMessage } from "./ChatMessage";
import {
  BUBBLE_LABEL_CLASS,
  BUBBLE_LABEL_STYLE,
  BUBBLE_SHELL_CLASS,
  USER_BUBBLE_LAYOUT_CLASS,
  USER_BUBBLE_STYLE,
} from "./bubble";
import { turnPlainText } from "./utils";

// ---------------------------------------------------------------------------
// MessageRow — 一条时间线消息及其操作行。
//
// 操作行常驻行高、hover 只改透明度，消息列表不因悬停而跳动。用户消息右对齐
// 「时间 · 复制 · 编辑」，助手消息左对齐「复制 · 时间」，两侧行高一致。
// 不可编辑时编辑按钮不渲染（不置灰、不加 tooltip）——入口的存在本身即判据。
// ---------------------------------------------------------------------------

/** 后果说明用的琥珀色：语义是「这一步的代价」，与错误红区分开。 */
const AMBER = "oklch(0.80 0.12 80)";

interface MessageRowProps {
  turn: Turn;
  /** 该 turn 是流式草稿——末尾块处于生成中，不给操作行。 */
  streaming?: boolean;
  /** 此刻是否给出改写入口（判据见 utils.canEditUserTurn）。 */
  editable?: boolean;
  /** 该条消息是否处于原地编辑态。 */
  editing?: boolean;
  /** 改写请求在途——编辑器锁定。 */
  submitting?: boolean;
  onStartEdit?: (turnUuid: string, text: string) => void;
  onCancelEdit?: () => void;
  onSubmitEdit?: (turnUuid: string, text: string) => void;
}

export function MessageRow({
  turn,
  streaming,
  editable = false,
  editing = false,
  submitting = false,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
}: MessageRowProps) {
  const { t } = useTranslation("dashboard");
  const text = turnPlainText(turn);
  const time = formatClockTime(turn.timestamp);
  const turnUuid = turn.uuid;

  if (editing && turnUuid) {
    // 编辑期间会话可能开跑或弹出问答卡片。此时不关编辑器（用户写到一半的草稿不能
    // 被夺走），但重新发送要跟着 editable 一起关：否则一次改写会把刚开的那一轮
    // 连同它已经做出的文件修改一起作废。
    return (
      <MessageEditor
        initialText={text}
        submitting={submitting}
        canSubmit={editable}
        onCancel={() => onCancelEdit?.()}
        onSubmit={(draft) => onSubmitEdit?.(turnUuid, draft)}
      />
    );
  }

  // 操作行只服务于有正文的已落库消息：草稿、工具卡片、系统事件没有可复制的正文
  const showActions = !streaming && Boolean(text.trim()) && (turn.type === "user" || turn.type === "assistant");
  if (!showActions) {
    return <ChatMessage message={turn} streaming={streaming} />;
  }

  const isUser = turn.type === "user";

  return (
    <div className="group">
      <ChatMessage message={turn} />
      <div
        className={`flex h-7 items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 ${
          isUser ? "justify-end pr-0.5" : "pl-0.5"
        }`}
      >
        {isUser && time && <TimeStamp time={time} className="mr-1" />}
        <CopyButton text={text} />
        {isUser && editable && turnUuid && (
          <button
            type="button"
            onClick={() => onStartEdit?.(turnUuid, text)}
            title={t("message_edit")}
            aria-label={t("message_edit")}
            className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10"
            style={{ color: "var(--color-text-3)" }}
          >
            <Pencil aria-hidden className="h-3.5 w-3.5" />
          </button>
        )}
        {!isUser && time && <TimeStamp time={time} className="ml-1" />}
      </div>
    </div>
  );
}

function TimeStamp({ time, className }: { time: string; className: string }) {
  return (
    <span className={`${className} text-[10.5px] tabular-nums`} style={{ color: "var(--color-text-4)" }}>
      {time}
    </span>
  );
}

/** 无底色图标按钮：hover 出灰色圆角方形背景，复制成功后图标短暂变对勾。 */
function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const Icon = copied ? Check : Copy;
  const label = copied ? t("message_copied") : t("message_copy");

  return (
    <button
      type="button"
      onClick={() => {
        // 复制成功才给对勾：非安全上下文走 execCommand 兜底，兜底也失败时不假报成功
        void copyText(text).then(
          () => setCopied(true),
          () => undefined,
        );
      }}
      title={label}
      aria-label={label}
      className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10"
      style={{ color: copied ? "var(--color-accent-2)" : "var(--color-text-3)" }}
    >
      <Icon aria-hidden className="h-3.5 w-3.5" />
    </button>
  );
}

// ---------------------------------------------------------------------------
// MessageEditor — 气泡原地变编辑框，形态与位置不变。
// ---------------------------------------------------------------------------

function MessageEditor({
  initialText,
  submitting,
  canSubmit,
  onCancel,
  onSubmit,
}: {
  initialText: string;
  submitting: boolean;
  /** 此刻允许提交改写；false 时保留草稿但锁住重新发送。 */
  canSubmit: boolean;
  onCancel: () => void;
  onSubmit: (text: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [draft, setDraft] = useState(initialText);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 部分输入法在组合确认的那次 keydown 上不置 isComposing，靠组合事件补齐
  const isComposingRef = useRef(false);

  // 进入编辑态即聚焦、光标置尾、按内容撑开高度
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const submit = useCallback(() => {
    if (submitting || !canSubmit || !draft.trim()) return;
    onSubmit(draft);
  }, [draft, submitting, canSubmit, onSubmit]);

  return (
    <div className={`${USER_BUBBLE_LAYOUT_CLASS} ${BUBBLE_SHELL_CLASS}`} style={USER_BUBBLE_STYLE}>
      <div
        className={BUBBLE_LABEL_CLASS}
        style={{ ...BUBBLE_LABEL_STYLE, color: "var(--color-accent-2)" }}
      >
        {t("message_edit_title")}
      </div>
      <textarea
        ref={textareaRef}
        value={draft}
        rows={2}
        disabled={submitting}
        aria-label={t("message_edit_textarea_label")}
        onChange={(e) => {
          setDraft(e.target.value);
          e.currentTarget.style.height = "auto";
          e.currentTarget.style.height = `${e.currentTarget.scrollHeight}px`;
        }}
        onCompositionStart={() => {
          isComposingRef.current = true;
        }}
        onCompositionEnd={() => {
          isComposingRef.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            if (submitting) return;
            e.preventDefault();
            onCancel();
          } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            // 组合中的这一下确认的是候选词，不是提交（与主输入框同口径）
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || isComposingRef.current) return;
            e.preventDefault();
            submit();
          }
        }}
        className="focus-ring w-full resize-none rounded-md px-2 py-1.5 text-[12.5px] leading-[1.55] disabled:opacity-60"
        style={{
          background: "oklch(0.17 0.01 260 / 0.6)",
          border: "1px solid var(--color-hairline-soft)",
          color: "var(--color-text)",
        }}
      />
      <p className="mt-1.5 flex items-start gap-1 text-[10.5px] leading-[1.5]" style={{ color: AMBER }}>
        <TriangleAlert aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
        {t("message_edit_consequence")}
      </p>
      <div className="mt-2 flex items-center justify-end gap-2">
        <button
          type="button"
          // 改写在途时取消无从撤回请求：关掉编辑器只会让随后的会话切换显得无端
          disabled={submitting}
          onClick={onCancel}
          className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{ color: "var(--color-text-3)", border: "1px solid var(--color-hairline-soft)" }}
        >
          {t("message_edit_cancel")}
        </button>
        <button
          type="button"
          disabled={submitting || !canSubmit || !draft.trim()}
          onClick={submit}
          title={t("message_edit_resend_hint")}
          className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: "var(--color-accent)", color: "oklch(0.12 0 0)" }}
        >
          {submitting ? t("message_edit_resending") : t("message_edit_resend")}
        </button>
      </div>
    </div>
  );
}
