import type { ContentBlock, Turn } from "@/types";
import { cn } from "./utils";
import { getRoleLabel } from "./utils";
import { ContentBlockRenderer } from "./ContentBlockRenderer";

// ---------------------------------------------------------------------------
// ChatMessage – renders a full conversation turn (user, assistant, or system).
//
// Turns are normalised by the backend and consumed as strict Turn payloads.
// ---------------------------------------------------------------------------

interface ChatMessageProps {
  message: Turn;
}

export function ChatMessage({ message }: ChatMessageProps) {
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

  // Determine styling based on message type
  const isUser = messageType === "user";
  const isSystem = messageType === "system";

  const containerStyle: React.CSSProperties = isUser
    ? {
        marginLeft: "auto",
        maxWidth: "85%",
        background:
          "linear-gradient(180deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.06))",
        border: "1px solid var(--color-accent-soft)",
      }
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
    color: isUser ? "var(--color-accent-2)" : "var(--color-text-4)",
    letterSpacing: "0.06em",
  };

  return (
    <article
      className={cn("rounded-xl px-2.5 py-1.5 min-w-0")}
      style={containerStyle}
    >
      <div
        className="mb-1 text-[10px] font-semibold uppercase"
        style={labelStyle}
      >
        {getRoleLabel(messageType)}
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
