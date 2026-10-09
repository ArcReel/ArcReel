import { useLayoutEffect, useRef, useState } from "react";
import { CircleAlert, Clock, Pencil, Send, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useAssistantStore } from "@/stores/assistant-store";
import type { QueuedMessage, QueuedMessageWithdrawal } from "@/types";
import { voidCall } from "@/utils/async";
import { turnPlainText } from "./chat/utils";

// ---------------------------------------------------------------------------
// QueuedMessageTray — 输入框正上方的排队消息托盘。
// 回复进行中发出、Agent 尚未接纳的消息按发送顺序堆叠；被接纳的消息离开托盘，
// 作为用户消息出现在时间线上。没有排队消息时不渲染。
// 每条消息行尾是逐条操作，顺序为（立即发送、）编辑、删除：编辑把内容退回输入框，删除直接丢弃，
// 两者都要先向 Agent 撤回，撤回请求在途时这条消息的操作暂不可用。
// Agent 进程退出时仍在排队的消息转为「未发送」：托盘顶部说明会话已中断，这些消息行尾多一个发送，
// 由创作者决定重新发送、编辑或删除，不自动重发。
// 高度上限是 Agent 面板高度的 30%，与待办清单一致，超出后在托盘内滚动，输入框与发送按钮不被挤出面板。
// ---------------------------------------------------------------------------

interface QueuedMessageTrayProps {
  /** 编辑或删除一条排队消息。 */
  onWithdraw: (id: string, intent: QueuedMessageWithdrawal) => Promise<void>;
  /** 重新发送一条「未发送」消息。 */
  onResend: (id: string) => Promise<void>;
  /** 发送请求在途：受理后会清空输入框，此时退回的内容会被一并清掉，暂不能编辑。 */
  editDisabled: boolean;
}

export function QueuedMessageTray({ onWithdraw, onResend, editDisabled }: QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const messages = useAssistantStore((s) => s.queuedMessages);
  const listRef = useRef<HTMLUListElement>(null);
  const lastId = messages.at(-1)?.id;

  // 新发出的消息排在末尾：末尾换了一条就把它滚到托盘底边，前面的消息被接纳离开时不跳动。
  // 只滚托盘自身：面板收起时宽度为 0，scrollIntoView 会连带滚动外层裁切容器
  useLayoutEffect(() => {
    const list = listRef.current;
    const last = list?.lastElementChild;
    if (list && last instanceof HTMLElement) list.scrollTop = last.offsetTop + last.offsetHeight - list.clientHeight;
  }, [lastId]);

  if (messages.length === 0) return null;
  const hasUnsent = messages.some((message) => message.state === "unsent");

  return (
    <>
      {hasUnsent && (
        <p className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <CircleAlert aria-hidden className="size-3.5 shrink-0" />
          {t("queued_messages_unsent_notice")}
        </p>
      )}
      {/* 列表自身可聚焦，键盘才能滚动 */}
      <ul
        ref={listRef}
        aria-label={t("queued_messages_label")}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 只读的滚动区域需要键盘聚焦才能滚动
        tabIndex={0}
        className="relative mb-2 flex max-h-[30cqh] flex-col gap-1 overflow-y-auto rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {messages.map((message) => (
          <QueuedMessageItem
            key={message.id}
            message={message}
            onWithdraw={onWithdraw}
            onResend={onResend}
            editDisabled={editDisabled}
          />
        ))}
      </ul>
    </>
  );
}

function QueuedMessageItem({
  message,
  onWithdraw,
  onResend,
  editDisabled,
}: { message: QueuedMessage } & QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const [withdrawing, setWithdrawing] = useState(false);
  const withdraw = (intent: QueuedMessageWithdrawal) => {
    setWithdrawing(true);
    // 撤回成功时这一行随排队消息移出而卸载，复位只对仍在托盘里的行生效
    voidCall(onWithdraw(message.id, intent).finally(() => setWithdrawing(false)));
  };
  const [resending, setResending] = useState(false);
  const resend = () => {
    setResending(true);
    voidCall(onResend(message.id).finally(() => setResending(false)));
  };
  const busy = withdrawing || resending;
  const unsent = message.state === "unsent";
  const text = turnPlainText({ type: "user", content: message.content }).trim();
  const imageCount = message.content.filter((block) => block.type === "image").length;

  return (
    <li className="flex items-start gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
      <Clock aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        {text && <p className="line-clamp-2 whitespace-pre-wrap break-words">{text}</p>}
        {imageCount > 0 && <p className="text-muted-foreground">{t("queued_message_images", { count: imageCount })}</p>}
      </div>
      <span className="shrink-0 text-muted-foreground">
        {t(unsent ? "queued_message_state_unsent" : "queued_message_state_queued")}
      </span>
      {/* #3108 的「立即发送」排在编辑之前；「未发送」消息的发送排在最前 */}
      <div className="-my-0.5 flex shrink-0 items-center">
        {unsent && (
          <Button variant="ghost" size="icon-xs" disabled={busy} onClick={resend} aria-label={t("queued_message_resend")}>
            <Send aria-hidden />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={busy || editDisabled}
          onClick={() => withdraw("edit")}
          aria-label={t("queued_message_edit")}
        >
          <Pencil aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={busy}
          onClick={() => withdraw("delete")}
          aria-label={t("queued_message_delete")}
        >
          <Trash2 aria-hidden />
        </Button>
      </div>
    </li>
  );
}
