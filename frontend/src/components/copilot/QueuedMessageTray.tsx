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

// ---------------------------------------------------------------------------
// QueuedMessageTray sits directly above inputs.
// Messages sent during replies but not yet accepted stack in send order; accepted messages leave trays
// and become timeline user messages. Empty queues render nothing.
// Per-message end actions: Send now/Edit/Delete. Send now interrupts current Agent turns to handle messages first; Edit returns contents
// to inputs. All three first recall from Agent; pending requests disable that message actions.
// Deletion is irreversible: Delete replaces rows inline with Confirm/Cancel and focuses Cancel to avoid accidental Enter deletion;
// cancel/Esc exits confirmation and returns focus to Delete; leaving confirmation regions also exits. Both buttons disable during confirmed requests,
// failures remain in confirmation, and success removes rows with messages.
// During Agent questions, trays remain beneath questionnaires without Send now, avoiding interrupted questions.
// Messages still queued when Agent processes exit become Unsent; trays explain interrupted sessions and add Send actions,
// leaving creators to resend/edit/delete without automatic resending.
// Cap at 30% of Agent-panel heights like todos; overflowing trays scroll internally, never squeezing inputs/Send buttons outside panels.
// Questions share questionnaire height with trays, capped at 15% to preserve message space.
// ---------------------------------------------------------------------------

interface QueuedMessageTrayProps {
  /** Edit/delete queued messages. */
  onWithdraw: (id: string, intent: QueuedMessageWithdrawal) => Promise<void>;
  /** Resend Unsent messages. */
  onResend: (id: string) => Promise<void>;
  /** Immediately send queued messages. */
  onSendNow: (id: string) => Promise<void>;
  /** Agent questions coexist with trays/questionnaires, reducing height caps and omitting Send now. */
  questionPending: boolean;
  /** Pending sends clear inputs on acceptance, potentially clearing recalled contents too; temporarily disable editing. */
  editDisabled: boolean;
}

export function QueuedMessageTray(props: QueuedMessageTrayProps) {
  const { t } = useTranslation("dashboard");
  const messages = useAssistantStore((s) => s.queuedMessages);
  const listRef = useRef<HTMLUListElement>(null);
  // Previous-render ordered tray message IDs distinguish newly sent end messages from reordered replacements.
  const previousIdsRef = useRef<string[]>([]);

  // New sends append; unseen end IDs scroll to tray bottoms without jumping when earlier accepted messages leave.
  // Send now moves existing messages first; retained original first messages indicate reordering, so scroll to tops to reveal them.
  // Scroll trays only: collapsed panels have zero width and scrollIntoView also scrolls outer clipping containers.
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
      {/* Focusable lists enable keyboard scrolling. */}
      <ul
        ref={listRef}
        aria-label={t("queued_messages_label")}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Read-only scroll regions require keyboard focus for scrolling
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
  // Edit/Delete/Send now first recall messages from Agent.
  const withdrawThen = (request: () => Promise<void>) => {
    setWithdrawing(true);
    // Successful recall unmounts rows as messages leave; resets affect only rows remaining in trays.
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
  // Confirmation focuses Cancel; pending requests disable both buttons. Failed requests retain rows; resetting busy returns focus to Cancel.
  // Cancel/Esc exits and returns focus to Delete; leaving regions (Tab/click elsewhere) also exits but leaves focus at user destinations.
  // Successful confirmation removes rows with messages.
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
        // At minimum 320px panel widths, hints/two buttons cannot share rows; permit wrapping with hints moving above first.
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
          {/* Send for Unsent and Send now for queued messages both come first. */}
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
