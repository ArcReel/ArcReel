import React from "react";
import htm from "htm";
import { cn, getRoleLabel } from "../../utils.js";
import { ContentBlockRenderer } from "./ContentBlockRenderer.js";

const html = htm.bind(React.createElement);

/**
 * ChatMessage - Renders a conversation turn.
 *
 * A turn represents a complete interaction unit from the backend grouper:
 * - User turn: user's input message
 * - Assistant turn: all assistant content blocks with tool results attached
 * - Result turn: session completion marker
 * - System turn: system-injected content (rare)
 *
 * The backend (transcript_reader.py) handles:
 * - Merging consecutive assistant messages into one turn
 * - Attaching tool_result to corresponding tool_use blocks
 * - Attaching skill_content to Skill tool blocks
 */
export function ChatMessage({ message }) {
    if (!message) return null;

    const messageType = message.type || message.role || "unknown";
    const content = message.content;

    // Normalize content to array
    const blocks = normalizeContent(content);

    // Skip empty messages (except result which has no content)
    if (blocks.length === 0 && messageType !== "result") {
        return null;
    }

    // Result message (session completion)
    if (messageType === "result") {
        const isSuccess = message.subtype === "success";
        return html`
            <article className=${cn(
                "rounded-xl px-3 py-2 border text-center",
                isSuccess
                    ? "border-emerald-400/30 bg-emerald-500/10"
                    : "border-red-400/30 bg-red-500/10"
            )}>
                <span className=${cn(
                    "text-xs font-medium",
                    isSuccess ? "text-emerald-400" : "text-red-400"
                )}>
                    ${isSuccess ? "会话完成" : "会话出错"}
                </span>
            </article>
        `;
    }

    // Determine styling based on message type
    const isUser = messageType === "user";
    const isSystem = messageType === "system";

    const containerClass = isUser
        ? "ml-8 bg-neon-500/15 border-neon-400/25"
        : isSystem
            ? "mr-3 bg-slate-800/30 border-slate-600/20"
            : "mr-3 bg-white/5 border-white/10";

    return html`
        <article className=${cn("rounded-xl px-3 py-2 border", containerClass)}>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">
                ${getRoleLabel(messageType)}
            </div>
            <div className="text-sm text-slate-100 leading-6">
                ${blocks.map((block, index) => html`
                    <${ContentBlockRenderer} key=${block.id || index} block=${block} index=${index} />
                `)}
            </div>
        </article>
    `;
}

/**
 * Normalize content to an array of content blocks.
 */
function normalizeContent(content) {
    // Already an array
    if (Array.isArray(content)) {
        return content;
    }

    // String content - wrap in text block
    if (typeof content === "string") {
        const trimmed = content.trim();
        if (!trimmed) return [];

        // Try to parse as JSON array
        if (trimmed.startsWith("[")) {
            try {
                const parsed = JSON.parse(trimmed);
                if (Array.isArray(parsed)) {
                    return parsed;
                }
            } catch {
                // Not valid JSON, treat as plain text
            }
        }

        return [{ type: "text", text: content }];
    }

    return [];
}
