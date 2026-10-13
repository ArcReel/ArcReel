import { type FocusEvent, type KeyboardEvent, type PointerEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, CircleAlert, Clock, Pencil, Send, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useAssistantStore } from "@/stores/assistant-store";
import type { QueuedMessage, QueuedMessageWithdrawal } from "@/types";
import { voidCall } from "@/utils/async";
import { turnPlainText } from "./chat/utils";

// ---------------------------------------------------------------------------
// QueuedMessageTray — 输入框正上方的排队消息托盘。
// 回复进行中发出、Agent 尚未接纳的消息按发送顺序堆叠；被接纳的消息离开托盘，
// 作为用户消息出现在时间线上。没有排队消息时不渲染。
// 每条消息行尾是逐条操作，顺序为立即发送、编辑、删除：立即发送让 Agent 打断当前轮先处理它，编辑把内容
// 退回输入框。三者都要先向 Agent 撤回，请求在途时这条消息的操作暂不可用。
// 删除不可撤销，点删除后在这一行原地换成确认与取消，焦点落在取消上，误按 Enter 不会删除；
// 取消或 Esc 退出确认并把焦点交还删除按钮，焦点离开确认区域也退出确认。确认后请求在途时两个按钮禁用，
// 失败后仍停在确认态，成功后这一行随消息移出。
// Agent 提问期间托盘照常显示在问卷下方，只是不提供立即发送，免得打断提问。
// Agent 进程退出时仍在排队的消息转为「未发送」：托盘顶部说明会话已中断，这些消息行尾多一个发送，
// 由创作者决定重新发送、编辑或删除，不自动重发。
// 高度上限是 Agent 面板高度的 30%，与待办清单一致，超出后在托盘内滚动，输入框与发送按钮不被挤出面板。
// 提问期间与问卷分用问卷原有的高度，上限降到 15%，消息区仍留得出位置。
// ---------------------------------------------------------------------------

interface QueuedMessageTrayProps {
  /** 编辑或删除一条排队消息。 */
  onWithdraw: (id: string, intent: QueuedMessageWithdrawal) => Promise<void>;
  /** 重新发送一条「未发送」消息。 */
  onResend: (id: string) => Promise<void>;
  /** 立即发送一条排队消息。 */
  onSendNow: (id: string) => Promise<void>;
  /** Agent 正在提问：托盘与问卷同时在场，降低高度上限，且不提供立即发送。 */
  questionPending: boolean;
  /** 发送请求在途：受理后会清空输入框，此时退回的内容会被一并清掉，暂不能编辑。 */
  editDisabled: boolean;
}

export function QueuedMessageTray(props: QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const messages = useAssistantStore((s) => s.queuedMessages);
  const listRef = useRef<HTMLUListElement>(null);
  // 上一次渲染时托盘里的消息 id，按顺序；据此分辨末尾是新发出的消息还是重排后换上来的
  const previousIdsRef = useRef<string[]>([]);

  // 新发出的消息排在末尾：末尾出现没见过的消息就把它滚到托盘底边，前面的消息被接纳离开时不跳动。
  // 立即发送把已有消息排到最前：原先的首条仍在时说明是重排，滚到顶边露出它。
  // 只滚托盘自身：面板收起时宽度为 0，scrollIntoView 会连带滚动外层裁切容器
  useLayoutEffect(() => {
    const list = listRef.current;
    const ids = messages.map((message) => message.id);
    const previousIds = previousIdsRef.current;
    previousIdsRef.current = ids;
    if (!list || ids.length === 0) return;
    const last = list.lastElementChild;
    if (!previousIds.includes(ids[ids.length - 1])) {
      if (last instanceof HTMLElement) list.scrollTop = last.offsetTop + last.offsetHeight - list.clientHeight;
    } else if (ids[0] !== previousIds[0] && previousIds[0] !== undefined && ids.includes(previousIds[0])) {
      list.scrollTop = 0;
    }
  }, [messages]);

  if (messages.length === 0) return null;
  const hasUnsent = messages.some((message) => message.state === "unsent");

  return (
    <>
      {hasUnsent && (
        <p className="-mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
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
        className={cn(
          "relative flex flex-col gap-1 overflow-y-auto rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
          props.questionPending ? "max-h-[15cqh]" : "max-h-[30cqh]",
        )}
      >
        {messages.map((message) => (
          <QueuedMessageItem key={message.id} message={message} {...props} />
        ))}
      </ul>
    </>
  );
}

function QueuedMessageItem({
  message,
  onWithdraw,
  onResend,
  onSendNow,
  questionPending,
  editDisabled,
}: { message: QueuedMessage } & QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const [withdrawing, setWithdrawing] = useState(false);
  // 编辑、删除与立即发送都先向 Agent 撤回
  const withdrawThen = (request: () => Promise<void>) => {
    setWithdrawing(true);
    // 撤回成功时这一行随排队消息移出而卸载，复位只对仍在托盘里的行生效
    voidCall(request().finally(() => setWithdrawing(false)));
  };
  const withdraw = (intent: QueuedMessageWithdrawal) => withdrawThen(() => onWithdraw(message.id, intent));
  const [resending, setResending] = useState(false);
  const resend = () => {
    setResending(true);
    voidCall(onResend(message.id).finally(() => setResending(false)));
  };
  const busy = withdrawing || resending;
  const unsent = message.state === "unsent";
  const text = turnPlainText({ type: "user", content: message.content }).trim();
  const imageCount = message.content.filter((block) => block.type === "image").length;
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const cancelDeleteRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef(false);
  // 确认态焦点落在取消上；请求在途时两个按钮禁用，请求失败后这一行还在，busy 复位时焦点回到取消。
  // 取消或 Esc 退出确认态时把焦点交还删除按钮；焦点离开确认区域（Tab 走或点别处）也退出，焦点留在用户去的地方。
  // 确认成功后这一行随消息移出而卸载。
  useEffect(() => {
    if (confirmingDelete) {
      if (!busy) cancelDeleteRef.current?.focus();
    } else if (returnFocusRef.current) {
      returnFocusRef.current = false;
      deleteRef.current?.focus();
    }
  }, [confirmingDelete, busy]);
  const dismissDelete = (returnFocus: boolean) => {
    if (busy) return;
    returnFocusRef.current = returnFocus;
    setConfirmingDelete(false);
  };
  const dismissOnEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      dismissDelete(true);
    }
  };
  const dismissOnFocusLeave = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget;
    // 焦点仍在区域内，或整个窗口失焦（relatedTarget 为空且文档已不持有焦点）：确认态保留
    if (next instanceof Node ? event.currentTarget.contains(next) : !document.hasFocus()) return;
    dismissDelete(false);
  };
  const keepFocusInside = (event: PointerEvent<HTMLDivElement>) => {
    // 点区域内的提示文字不让焦点落到 body：按钮（含按钮内的图标、文字节点）照常接收焦点
    if (event.target instanceof Element && event.target.closest("button")) return;
    event.preventDefault();
  };

  return (
    <li className="flex items-start gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
      <Clock aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        {text && <p className="line-clamp-2 whitespace-pre-wrap break-words">{text}</p>}
        {imageCount > 0 && <p className="text-muted-foreground">{t("queued_message_images", { count: imageCount })}</p>}
      </div>
      {confirmingDelete ? (
        // 面板最窄 320px 时提示文字加两个按钮放不下一行：允许换行，宽度不够时提示文字先折到上一行。
        // Esc 绑在两个按钮上而不是这个容器上：容器不是交互元素，jsx-a11y 不允许在它上面挂键盘事件；
        // 焦点一离开容器确认态就结束，Esc 不会落在容器外
        <div
          role="group"
          aria-label={t("queued_message_delete_prompt")}
          className="-my-0.5 flex min-w-0 flex-wrap items-center justify-end gap-1"
          onBlur={dismissOnFocusLeave}
          onPointerDown={keepFocusInside}
        >
          <span className="text-muted-foreground">{t("queued_message_delete_prompt")}</span>
          <Button variant="destructive" size="xs" disabled={busy} onKeyDown={dismissOnEscape} onClick={() => withdraw("delete")}>
            {t("queued_message_delete_confirm")}
          </Button>
          <Button
            ref={cancelDeleteRef}
            variant="outline"
            size="xs"
            disabled={busy}
            onKeyDown={dismissOnEscape}
            onClick={() => dismissDelete(true)}
          >
            {t("cancel")}
          </Button>
        </div>
      ) : (
        <>
          <span className="shrink-0 text-muted-foreground">
            {t(unsent ? "queued_message_state_unsent" : "queued_message_state_queued")}
          </span>
          {/* 「未发送」消息的发送与排队中消息的立即发送都排在最前 */}
          <div className="-my-0.5 flex shrink-0 items-center">
            {unsent && (
              <Button variant="ghost" size="icon-xs" disabled={busy} onClick={resend} aria-label={t("queued_message_resend")}>
                <Send aria-hidden />
              </Button>
            )}
            {!unsent && !questionPending && (
              <Button
                variant="ghost"
                size="icon-xs"
                disabled={busy}
                onClick={() => withdrawThen(() => onSendNow(message.id))}
                aria-label={t("queued_message_send_now")}
              >
                <ArrowUp aria-hidden />
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
              ref={deleteRef}
              variant="ghost"
              size="icon-xs"
              disabled={busy}
              onClick={() => setConfirmingDelete(true)}
              aria-label={t("queued_message_delete")}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
        </>
      )}
    </li>
  );
}
