import React from "react";
import htm from "htm";
import { TextBlock } from "./TextBlock.js";
import { ToolCallWithResult } from "./ToolCallWithResult.js";
import { ThinkingBlock } from "./ThinkingBlock.js";
import { SkillContentBlock } from "./SkillContentBlock.js";

const html = htm.bind(React.createElement);

function stringifySafely(value) {
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function normalizeToolResultContent(content) {
    if (typeof content === "string") {
        return content;
    }

    if (content === null || content === undefined) {
        return "";
    }

    if (Array.isArray(content)) {
        const textParts = content
            .map((item) => normalizeToolResultContent(item))
            .filter((item) => typeof item === "string" && item.trim().length > 0);
        return textParts.length > 0 ? textParts.join("\n") : stringifySafely(content);
    }

    if (typeof content === "object") {
        if (typeof content.text === "string") {
            return content.text;
        }
        return stringifySafely(content);
    }

    return String(content);
}

/**
 * ContentBlockRenderer - Renders a single content block within a turn.
 *
 * Block types:
 * - text: Plain text or markdown
 * - tool_use: Tool call (with optional result, skill_content attached by grouper)
 * - tool_result: Standalone tool result (rarely used, usually attached to tool_use)
 * - thinking: Claude's thinking block
 * - skill_content: Standalone skill content (rarely used, usually attached to tool_use)
 */
export function ContentBlockRenderer({ block, index }) {
    if (!block || typeof block !== "object") {
        return null;
    }

    const blockType = block.type || "text";
    const key = block.id || `block-${index}`;

    switch (blockType) {
        case "text":
            return html`<${TextBlock} key=${key} text=${block.text} />`;

        case "tool_use":
            // Use unified ToolCallWithResult for all tool calls
            // This handles both regular tools and Skill tools
            // Result and skill_content are attached by the backend grouper
            return html`<${ToolCallWithResult} key=${key} block=${block} />`;

        case "tool_result":
            // Standalone tool_result (should be rare - usually attached to tool_use)
            // Render as a simple result block
            const normalizedResultText = normalizeToolResultContent(block.content);
            return html`
                <div key=${key} className="my-1.5 rounded-lg border border-white/10 bg-ink-800/30 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                        ${block.is_error ? "执行失败" : "工具结果"}
                    </div>
                    <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
                        ${normalizedResultText}
                    </pre>
                </div>
            `;

        case "skill_content":
            // Standalone skill content (should be rare - usually attached to tool_use)
            return html`<${SkillContentBlock} key=${key} text=${block.text} />`;

        case "thinking":
            return html`<${ThinkingBlock} key=${key} thinking=${block.thinking} />`;

        default:
            // Fallback: render as text
            const text = block.text || block.content || JSON.stringify(block);
            return html`<${TextBlock} key=${key} text=${text} />`;
    }
}
