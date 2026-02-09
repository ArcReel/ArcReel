import React from "react";
import htm from "htm";
import { TextBlock } from "./TextBlock.js";
import { ToolUseBlock } from "./ToolUseBlock.js";
import { ToolResultBlock } from "./ToolResultBlock.js";
import { ThinkingBlock } from "./ThinkingBlock.js";

const html = htm.bind(React.createElement);

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
            return html`
                <${ToolUseBlock}
                    key=${key}
                    id=${block.id}
                    name=${block.name}
                    input=${block.input}
                />
            `;

        case "tool_result":
            return html`
                <${ToolResultBlock}
                    key=${key}
                    tool_use_id=${block.tool_use_id}
                    content=${block.content}
                    is_error=${block.is_error}
                />
            `;

        case "thinking":
            return html`<${ThinkingBlock} key=${key} thinking=${block.thinking} />`;

        default:
            // Fallback: render as text
            const text = block.text || block.content || JSON.stringify(block);
            return html`<${TextBlock} key=${key} text=${text} />`;
    }
}
