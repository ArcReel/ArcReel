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


interface MessageRowProps {
  turn: Turn;
  streaming?: boolean;
  announce?: boolean;
  editable?: boolean;
  editing?: boolean;
  submitting?: boolean;
  onStartEdit?: (turnUuid: string, text: string) => void;
  onCancelEdit?: () => void;
  onSubmitEdit?: (turnUuid: string, text: string, images: ImagePayload[]) => void;
}

export function MessageRow({
  turn,
  streaming = false,
  announce = false,
  editable = false,
  editing = false,
  submitting = false,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
}: MessageRowProps) {
  const turnUuid = turn.uuid;

  if (turn.type === "user" && editing && turnUuid) {
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
      return <SystemEvent turn={turn} announce={announce} />;
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
                  {images.map((src, index) => (
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 图片块没有 id，顺序即身份，消息落库后不再重排或过滤
                    <ChatImage key={index} src={src} index={index + 1} />
                  ))}
                </div>
              )}
              {blocks.map((block, index) => (
                // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 优先用块 id；无 id 的块只会在末尾追加，不重排不过滤，序号即身份
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

function SystemEvent({ turn, announce }: { turn: Turn; announce: boolean }) {
  const blocks = turn.content ?? [];
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {blocks.map((block, index) => (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 优先用块 id；无 id 的块只会在末尾追加，不重排不过滤，序号即身份
        <ContentBlockRenderer key={block.id ?? index} block={block} index={index} announce={announce} />
      ))}
    </div>
  );
}

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
