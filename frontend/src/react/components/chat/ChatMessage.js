import React from "react";
import htm from "htm";
import { cn, getRoleLabel } from "../../utils.js";
import { ContentBlockRenderer } from "./ContentBlockRenderer.js";

const html = htm.bind(React.createElement);

/**
 * Get message type from SDK message format or legacy format
 */
function getMessageType(message) {
    // SDK messages use 'type' field (user, assistant, result, etc.)
    if (message.type) return message.type;
    // Legacy format uses 'role' field
    if (message.role) return message.role;
    return "unknown";
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
    const messageType = getMessageType(message);
    const isUser = messageType === "user";

    const blocks = normalizeContent(message);

    const containerClass = isUser
        ? "ml-8 bg-neon-500/15 border-neon-400/25"
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
