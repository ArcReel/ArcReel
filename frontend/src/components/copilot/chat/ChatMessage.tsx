import { useTranslation } from "react-i18next";
import type { ContentBlock, Turn } from "@/types";
import { cn } from "./utils";
import { getRoleLabel } from "./utils";
import { ContentBlockRenderer } from "./ContentBlockRenderer";
import { AgentFailureCard } from "./AgentFailureCard";
import {
  BUBBLE_LABEL_CLASS,
  BUBBLE_LABEL_STYLE,
  BUBBLE_SHELL_CLASS,
  USER_BUBBLE_LAYOUT_CLASS,
  USER_BUBBLE_STYLE,
} from "./bubble";

// ---------------------------------------------------------------------------
// ChatMessage – renders a full conversation turn (user, assistant, or system).
//
// Turns are normalised by the backend and consumed as strict Turn payloads.
// ---------------------------------------------------------------------------

interface ChatMessageProps {
  message: Turn;
  /** 该 turn 是流式草稿（draft）——末尾块处于生成中。 */
  streaming?: boolean;
}

export function ChatMessage({ message, streaming }: ChatMessageProps) {
  // hook 必须在下面各处早退之前调用。
  const { t } = useTranslation("dashboard");

  if (!message) return null;

  const messageType = typeof message.type === "string" ? message.type : "";
  if (!["user", "assistant", "system"].includes(messageType)) {
    return null;
  }

  const content = message.content;

  // Normalise content to array
  const blocks = normalizeContent(content);

  // Skip empty messages
  if (blocks.length === 0) {
    return null;
  }

  // Agent 故障是写入点定型的系统事件，不套用普通消息气泡或“系统”角色标签。
  const soleBlock = blocks.length === 1 ? blocks[0] : undefined;
  if (messageType === "system" && soleBlock?.type === "agent_failure" && soleBlock.failure) {
    return <AgentFailureCard failure={soleBlock.failure} />;
  }

  // Determine styling based on message type
  const isUser = messageType === "user";
  const isSystem = messageType === "system";

  const containerStyle: React.CSSProperties = isUser
    ? USER_BUBBLE_STYLE
    : isSystem
      ? {
          background: "oklch(0.22 0.011 265 / 0.5)",
          border: "1px solid var(--color-hairline-soft)",
        }
      : {
          background: "oklch(0.21 0.012 265 / 0.5)",
          border: "1px solid var(--color-hairline-soft)",
        };

  const labelStyle: React.CSSProperties = {
    ...BUBBLE_LABEL_STYLE,
    color: isUser ? "var(--color-accent-2)" : "var(--color-text-4)",
  };

  return (
    <article
      className={cn(BUBBLE_SHELL_CLASS, "min-w-0", isUser && USER_BUBBLE_LAYOUT_CLASS)}
      style={containerStyle}
    >
      <div className={BUBBLE_LABEL_CLASS} style={labelStyle}>
        {getRoleLabel(messageType, t)}
      </div>
      <div
        className="min-w-0 overflow-hidden text-[12.5px] leading-[1.55]"
        style={{ color: "var(--color-text)" }}
      >
        {blocks.map((block, index) => (
          <ContentBlockRenderer
            key={block.id ?? index}
            block={block}
            index={index}
            streaming={Boolean(streaming) && index === blocks.length - 1}
          />
        ))}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Normalise content to an array of ContentBlocks.
 */
function normalizeContent(content: ContentBlock[] | string | undefined): ContentBlock[] {
  // Already an array — backend guarantees normalized blocks
  if (Array.isArray(content)) {
    return content;
  }

  // String content — defensive fallback (backend should not send this)
  if (typeof content === "string") {
    const trimmed = content.trim();
    if (!trimmed) return [];
    return [{ type: "text", text: content }];
  }

  return [];
}
