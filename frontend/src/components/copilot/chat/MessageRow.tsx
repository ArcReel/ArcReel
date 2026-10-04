import { Pencil } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ContentBlock, ImagePayload, Turn } from "@/types";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Message, MessageContent } from "@/components/ui/message";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { CopyButton } from "@/components/shared/CopyButton";
import { formatClockTime } from "@/utils/date-format";
import { ChatImage } from "./ChatImage";
import { ContentBlockRenderer } from "./ContentBlockRenderer";
import { MessageEditor } from "./MessageEditor";
import { TextBlock } from "./TextBlock";
import { turnImageAttachments, turnPlainText } from "./utils";
import { segmentWorkBlocks } from "./work-label";

// ---------------------------------------------------------------------------
// MessageRow — 消息流里的一项，按 turn 类型分发：
//   user      → 靠右的浅色气泡，编辑态原地换成 MessageEditor
//   assistant → 无气泡的正文，正文限宽 40em；工序、思考等块交给 ContentBlockRenderer，
//               连续的工序合成一列单行工序
//   system    → 逐块交给 ContentBlockRenderer（中断分隔线、故障卡片、后台任务等）
//
// 操作行常驻行高、悬停或聚焦时才显示，消息列表不因悬停而跳动。用户消息右对齐
// 「时间、复制、编辑」，Agent 消息左对齐「复制、时间」。不可编辑时编辑按钮不渲染
// （不置灰、不加提示），入口是否存在本身就是判据。
// ---------------------------------------------------------------------------

interface MessageRowProps {
  turn: Turn;
  /** 该 turn 含流式草稿：末尾块仍在生成，不给操作行。 */
  streaming?: boolean;
  /** 此刻是否给出改写入口（判据见 utils.canEditUserTurn）。 */
  editable?: boolean;
  /** 该条消息是否处于原地编辑态。 */
  editing?: boolean;
  /** 改写请求在途，编辑器锁定。 */
  submitting?: boolean;
  onStartEdit?: (turnUuid: string, text: string) => void;
  onCancelEdit?: () => void;
  /** 提交改写。`images` 是编辑器里保留下来的图片附件，随改写后的文本一同提交。 */
  onSubmitEdit?: (turnUuid: string, text: string, images: ImagePayload[]) => void;
}

export function MessageRow({
  turn,
  streaming = false,
  editable = false,
  editing = false,
  submitting = false,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
}: MessageRowProps) {
  const turnUuid = turn.uuid;

  if (turn.type === "user" && editing && turnUuid) {
    // 编辑期间会话可能开跑或弹出问答卡片。此时不关编辑器（用户写到一半的内容不能
    // 被夺走），但重新发送要跟着 editable 一起关：否则一次改写会把刚开的那一轮
    // 连同它已经做出的文件修改一起作废。
    return (
      <MessageEditor
        initialText={turnPlainText(turn)}
        initialImages={turnImageAttachments(turn)}
        submitting={submitting}
        canSubmit={editable}
        onCancel={() => onCancelEdit?.()}
        onSubmit={(text, images) => onSubmitEdit?.(turnUuid, text, images)}
      />
    );
  }

  switch (turn.type) {
    case "user":
      return (
        <UserMessage
          turn={turn}
          onStartEdit={editable && turnUuid ? (text) => onStartEdit?.(turnUuid, text) : undefined}
        />
      );
    case "assistant":
      return <AgentMessage turn={turn} streaming={streaming} />;
    case "system":
      return <SystemEvent turn={turn} />;
    default:
      return null;
  }
}

function UserMessage({ turn, onStartEdit }: { turn: Turn; onStartEdit?: (text: string) => void }) {
  const { t } = useTranslation("dashboard");
  const blocks = turn.content ?? [];
  const text = turnPlainText(turn);
  const time = formatClockTime(turn.timestamp);
  const images = blocks.flatMap((block) =>
    block.type === "image" && block.source?.data
      ? [`data:${block.source.media_type};base64,${block.source.data}`]
      : [],
  );
  // 问答回执是 Agent 问卷的答复，不是用户写的消息，没有复制与改写
  const isAnswer = blocks.some((block) => block.type === "question_answer");
  const showActions = !isAnswer && (Boolean(text.trim()) || images.length > 0);

  return (
    <Message align="end">
      <MessageContent>
        <Bubble variant="tinted" align="end" className="max-w-[85%]">
          <BubbleContent>
            <div className="flex flex-col gap-2">
              {images.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {/* 图片块没有 id，顺序即身份，消息落库后不再变化 */}
                  {images.map((src, index) => (
                    <ChatImage key={index} src={src} index={index + 1} />
                  ))}
                </div>
              )}
              {blocks.map((block, index) => (
                <UserBlock key={block.id ?? index} block={block} index={index} />
              ))}
            </div>
          </BubbleContent>
        </Bubble>
        {showActions && (
          <ActionRow align="end">
            {time && <time className="mr-1 text-xs text-muted-foreground tabular-nums">{time}</time>}
            {Boolean(text.trim()) && <CopyButton text={text} />}
            {onStartEdit && (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("message_edit")}
                      onClick={() => onStartEdit(text)}
                    />
                  }
                >
                  <Pencil aria-hidden />
                </TooltipTrigger>
                <TooltipContent>{t("message_edit")}</TooltipContent>
              </Tooltip>
            )}
          </ActionRow>
        )}
      </MessageContent>
    </Message>
  );
}

/** 用户消息里的文字原样显示（不按 Markdown 解析），图片在气泡顶部单独成排。 */
function UserBlock({ block, index }: { block: ContentBlock; index: number }) {
  switch (block.type) {
    case "text":
      return block.text?.trim() ? <p className="whitespace-pre-wrap">{block.text}</p> : null;
    case "image":
      return null;
    default:
      return <ContentBlockRenderer block={block} index={index} />;
  }
}

function AgentMessage({ turn, streaming }: { turn: Turn; streaming: boolean }) {
  const blocks = turn.content ?? [];
  const text = turnPlainText(turn);
  const time = formatClockTime(turn.timestamp);

  return (
    <Message align="start">
      <MessageContent>
        {segmentWorkBlocks(blocks).map(({ work, items }) => {
          const renderBlock = ({ block, index }: { block: ContentBlock; index: number }) =>
            block.type === "text" ? (
              <div key={block.id ?? index} className="max-w-[40em]">
                <TextBlock text={block.text} />
              </div>
            ) : (
              <ContentBlockRenderer
                key={block.id ?? index}
                block={block}
                index={index}
                streaming={streaming && index === blocks.length - 1}
              />
            );
          // 连续的工序排成没有段距的一列，正文与工序之间才留段距
          return work ? (
            <div key={items[0].block.id ?? items[0].index} className="flex min-w-0 flex-col">
              {items.map(renderBlock)}
            </div>
          ) : (
            renderBlock(items[0])
          );
        })}
        {!streaming && Boolean(text.trim()) && (
          <ActionRow align="start">
            <CopyButton text={text} />
            {time && <time className="ml-1 text-xs text-muted-foreground tabular-nums">{time}</time>}
          </ActionRow>
        )}
      </MessageContent>
    </Message>
  );
}

function SystemEvent({ turn }: { turn: Turn }) {
  const blocks = turn.content ?? [];
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {blocks.map((block, index) => (
        <ContentBlockRenderer key={block.id ?? index} block={block} index={index} />
      ))}
    </div>
  );
}

/** 消息下方的操作行：占住行高，悬停所在消息或焦点进入时才显示。 */
function ActionRow({ align, children }: { align: "start" | "end"; children: React.ReactNode }) {
  return (
    <div
      className={
        align === "end"
          ? "flex h-7 items-center gap-0.5 self-end opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100"
          : "flex h-7 items-center gap-0.5 self-start opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100"
      }
    >
      {children}
    </div>
  );
}
