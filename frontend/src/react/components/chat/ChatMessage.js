import React from "react";
import htm from "htm";
import { cn, getRoleLabel } from "../../utils.js";
import { ContentBlockRenderer } from "./ContentBlockRenderer.js";

const html = htm.bind(React.createElement);

/**
 * Check if content blocks contain only tool_result blocks
 * In Claude API, tool_result messages are sent as "user" type,
 * but they should be displayed as system/tool messages, not user messages.
 */
function isToolResultMessage(blocks) {
    if (!Array.isArray(blocks) || blocks.length === 0) return false;
    return blocks.every(block => block.type === "tool_result");
}

/**
 * Get the effective message type for display purposes.
 * - Regular user messages (text from user) -> "user"
 * - Tool results (type=user but contains tool_result blocks) -> "tool_result"
 * - Assistant messages -> "assistant"
 * - Result messages -> "result"
 */
function getEffectiveMessageType(message, blocks) {
    const rawType = message.type || message.role || "unknown";

    // If this is a "user" message but contains only tool_result blocks,
    // treat it as a tool_result message for display purposes
    if (rawType === "user" && isToolResultMessage(blocks)) {
        return "tool_result";
    }

    return rawType;
}

/**
 * Normalize content to an array of content blocks
 */
function normalizeContent(message) {
    const content = message.content;

    // SDK AssistantMessage content is already an array of blocks
    if (Array.isArray(content)) {
        return content;
    }

    // If content is a string, try to parse as JSON first
    if (typeof content === "string") {
        // Check if it looks like a JSON array
        const trimmed = content.trim();
        if (trimmed.startsWith("[")) {
            try {
                const parsed = JSON.parse(trimmed);
                if (Array.isArray(parsed)) {
                    return parsed;
                }
            } catch {
                // Not valid JSON, fall through to text handling
            }
        }
        // Plain text - wrap in TextBlock
        return [{ type: "text", text: content }];
    }

    // Fallback: empty array
    return [];
}

export function ChatMessage({ message }) {
    const blocks = normalizeContent(message);
    const messageType = getEffectiveMessageType(message, blocks);

    // Tool result messages should be rendered differently - not as user messages
    const isUser = messageType === "user";
    const isToolResult = messageType === "tool_result";

    // Skip rendering empty tool_result messages or render them compactly
    if (isToolResult && blocks.length === 0) {
        return null;
    }

    const containerClass = isUser
        ? "ml-8 bg-neon-500/15 border-neon-400/25"
        : isToolResult
            ? "mr-3 bg-slate-800/30 border-slate-600/20"  // Subtle style for tool results
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
