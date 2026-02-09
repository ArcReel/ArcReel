import React from "react";
import htm from "htm";
import { cn, getRoleLabel } from "../../utils.js";
import { ContentBlockRenderer } from "./ContentBlockRenderer.js";

const html = htm.bind(React.createElement);

function normalizeContent(content) {
    // If content is already an array of blocks, return as-is
    if (Array.isArray(content)) {
        return content;
    }

    // If content is a string, wrap in TextBlock
    if (typeof content === "string") {
        return [{ type: "text", text: content }];
    }

    // Try to parse JSON string
    if (typeof content === "string" && content.startsWith("[")) {
        try {
            const parsed = JSON.parse(content);
            if (Array.isArray(parsed)) {
                return parsed;
            }
        } catch {
            // Not valid JSON, treat as text
        }
    }

    // Fallback: empty array
    return [];
}

export function ChatMessage({ message }) {
    const role = message.role || "assistant";
    const isUser = role === "user";

    const blocks = normalizeContent(message.content);

    const containerClass = isUser
        ? "ml-8 bg-neon-500/15 border-neon-400/25"
        : "mr-3 bg-white/5 border-white/10";

    return html`
        <article className=${cn("rounded-xl px-3 py-2 border", containerClass)}>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">
                ${getRoleLabel(role)}
            </div>
            <div className="text-sm text-slate-100 leading-6">
                ${blocks.map((block, index) => html`
                    <${ContentBlockRenderer} key=${block.id || index} block=${block} index=${index} />
                `)}
            </div>
        </article>
    `;
}
