import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, CircleAlert, Clock, Pencil, Send, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useAssistantStore } from "@/stores/assistant-store";
import type { QueuedMessage, QueuedMessageWithdrawal } from "@/types";
import { voidCall } from "@/utils/async";
import { dismissOnEscape, dismissOnFocusLeave, keepFocusInside } from "@/utils/inline-confirm";
import { turnPlainText } from "./chat/utils";


interface QueuedMessageTrayProps {
  onWithdraw: (id: string, intent: QueuedMessageWithdrawal) => Promise<void>;
  onResend: (id: string) => Promise<void>;
  onSendNow: (id: string) => Promise<void>;
  questionPending: boolean;
  editDisabled: boolean;
}

export function QueuedMessageTray(props: QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const messages = useAssistantStore((s) => s.queuedMessages);
  const listRef = useRef<HTMLUListElement>(null);
  const previousIdsRef = useRef<string[]>([]);

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
  const withdrawThen = (request: () => Promise<void>) => {
    setWithdrawing(true);
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
  return (
    <li className="flex items-start gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
      <Clock aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        {text && <p className="line-clamp-2 whitespace-pre-wrap break-words">{text}</p>}
        {imageCount > 0 && <p className="text-muted-foreground">{t("queued_message_images", { count: imageCount })}</p>}
      </div>
      {confirmingDelete ? (
        <div
          role="group"
          aria-label={t("queued_message_delete_prompt")}
          className="-my-0.5 flex min-w-0 flex-wrap items-center justify-end gap-1"
          onBlur={(event) => dismissOnFocusLeave(event, dismissDelete)}
          onPointerDown={keepFocusInside}
        >
          <span className="text-muted-foreground">{t("queued_message_delete_prompt")}</span>
          <Button variant="destructive" size="xs" disabled={busy} onKeyDown={(event) => dismissOnEscape(event, dismissDelete)} onClick={() => withdraw("delete")}>
            {t("queued_message_delete_confirm")}
          </Button>
          <Button
            ref={cancelDeleteRef}
            variant="outline"
            size="xs"
            disabled={busy}
            onKeyDown={(event) => dismissOnEscape(event, dismissDelete)}
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
