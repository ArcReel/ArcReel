import { Clock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAssistantStore } from "@/stores/assistant-store";
import type { QueuedMessage } from "@/types";
import { turnPlainText } from "./chat/utils";

// ---------------------------------------------------------------------------
// QueuedMessageTray — 输入框正上方的排队消息托盘。
// 回复进行中发出、Agent 尚未接纳的消息按发送顺序堆叠；被接纳的消息离开托盘，
// 作为用户消息出现在时间线上。没有排队消息时不渲染。
// ---------------------------------------------------------------------------

export function QueuedMessageTray() {
  const { t } = useTranslation("dashboard");
  const messages = useAssistantStore((s) => s.queuedMessages);
  if (messages.length === 0) return null;

  return (
    <ul aria-label={t("queued_messages_label")} className="mb-2 flex flex-col gap-1">
      {messages.map((message) => (
        <QueuedMessageItem key={message.id} message={message} />
      ))}
    </ul>
  );
}

function QueuedMessageItem({ message }: { message: QueuedMessage }) {
  const { t } = useTranslation("dashboard");
  const text = turnPlainText({ type: "user", content: message.content }).trim();
  const imageCount = message.content.filter((block) => block.type === "image").length;

  return (
    <li className="flex items-start gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
      <Clock aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        {text && <p className="line-clamp-2 whitespace-pre-wrap break-words">{text}</p>}
        {imageCount > 0 && <p className="text-muted-foreground">{t("queued_message_images", { count: imageCount })}</p>}
      </div>
      <span className="shrink-0 text-muted-foreground">{t("queued_message_state_queued")}</span>
    </li>
  );
}
