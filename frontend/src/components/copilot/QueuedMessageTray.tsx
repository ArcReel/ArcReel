import { useLayoutEffect, useRef } from "react";
import { Clock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAssistantStore } from "@/stores/assistant-store";
import type { QueuedMessage } from "@/types";
import { turnPlainText } from "./chat/utils";

// ---------------------------------------------------------------------------
// QueuedMessageTray — 输入框正上方的排队消息托盘。
// 回复进行中发出、Agent 尚未接纳的消息按发送顺序堆叠；被接纳的消息离开托盘，
// 作为用户消息出现在时间线上。没有排队消息时不渲染。
// 高度上限是 Agent 面板高度的 30%，与待办清单一致，超出后在托盘内滚动，输入框与发送按钮不被挤出面板。
// ---------------------------------------------------------------------------

export function QueuedMessageTray() {
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

  return (
    // 列表里没有可聚焦的元素，列表自身可聚焦，键盘才能滚动
    <ul
      ref={listRef}
      aria-label={t("queued_messages_label")}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 只读的滚动区域需要键盘聚焦才能滚动
      tabIndex={0}
      className="relative mb-2 flex max-h-[30cqh] flex-col gap-1 overflow-y-auto rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
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
