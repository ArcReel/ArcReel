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
// MessageRow dispatches message-flow entries by turn type:
//   user      → right-aligned pale bubbles, replaced in place by MessageEditor when editing
//   assistant → bubble-free bodies capped at 40em; work/thinking blocks go to ContentBlockRenderer,
//               with consecutive work blocks forming columns of single-line work rows
//   system    → blockwise ContentBlockRenderer (interrupt separators/failure cards/background tasks, etc.)
//
// Action rows reserve line height but appear only on hover/focus, preventing hover layout jumps. User messages right-align
// Time/Copy/Edit; Agent messages left-align Copy/Time. Uneditable messages omit Edit entirely
// without disabled buttons/hints; entry existence itself communicates availability.
// ---------------------------------------------------------------------------

interface MessageRowProps {
  turn: Turn;
  /** Turns containing streaming drafts; final blocks still generate, so omit action rows. */
  streaming?: boolean;
  /** System turns newly arriving during viewing; their failure cards announce to screen readers. */
  announce?: boolean;
  /** Whether rewrite entries are available now (utils.canEditUserTurn criteria). */
  editable?: boolean;
  /** Whether messages are currently edited inline. */
  editing?: boolean;
  /** Pending rewrite requests lock editors. */
  submitting?: boolean;
  onStartEdit?: (turnUuid: string, text: string) => void;
  onCancelEdit?: () => void;
  /** Submit rewrites; `images` retains editor image attachments alongside rewritten text. */
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
    // Sessions may start/questionnaires appear during editing. Keep editors open to preserve users’ ongoing
    // input, but disable Resend together with editable; otherwise rewrites would invalidate newly started turns
    // and their already-performed file changes.
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
  // Questionnaire receipts answer Agent questionnaires, not user-authored messages; omit Copy/Rewrite.
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
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Image blocks lack IDs; order is identity, with no reordering/filtering after persistence
                    <ChatImage key={index} src={src} index={index + 1} />
                  ))}
                </div>
              )}
              {blocks.map((block, index) => (
                // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Prefer block IDs; unidentified blocks append only without reordering/filtering, making indexes identity
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

/** Display user text unchanged without Markdown parsing; images have separate rows above bubbles. */
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
          // Consecutive work blocks form columns without paragraph gaps; only body/work boundaries add gaps.
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
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Prefer block IDs; unidentified blocks append only without reordering/filtering, making indexes identity
        <ContentBlockRenderer key={block.id ?? index} block={block} index={index} announce={announce} />
      ))}
    </div>
  );
}

/** Below-message action rows reserve height, appearing only on message hover/focus within. */
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
