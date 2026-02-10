import React from "react";
import htm from "htm";
import { cn, getRoleLabel, mapWithUniqueKeys } from "../../utils.js";
import { ContentBlockRenderer } from "./ContentBlockRenderer.js";

const html = htm.bind(React.createElement);

/**
 * ChatMessage - Renders a conversation turn.
 *
 * Turns are normalized by the backend and consumed as strict TurnV2 payloads.
 */

export function ChatMessage({ message }) {
    if (!message) return null;

    const messageType = typeof message.type === "string" ? message.type : "";
    if (!["user", "assistant", "system"].includes(messageType)) {
        return null;
    }
    const content = message.content;

    // Normalize content to array
    const blocks = normalizeContent(content);
    const renderBlocks = mapWithUniqueKeys(blocks, (block) => block?.id, "block");

    // Skip empty messages
    if (blocks.length === 0) {
        return null;
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
                ${renderBlocks.map(({ item: block, key, index }) => html`
                    <${ContentBlockRenderer} key=${key} block=${block} index=${index} />
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
