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

  const containerClass = isUser
    ? "ml-4 bg-neon-500/15 border-neon-400/25"
    : isSystem
      ? "bg-slate-800/30 border-slate-600/20"
      : "bg-white/5 border-white/10";

  return (
    <article className={cn("rounded-xl px-3 py-2 border min-w-0", containerClass)}>
      <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">
        {getRoleLabel(messageType)}
      </div>
      <div className="text-sm text-slate-100 leading-6 min-w-0 overflow-hidden">
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
