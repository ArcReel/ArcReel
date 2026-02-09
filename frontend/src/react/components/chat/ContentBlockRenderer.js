import React from "react";
import htm from "htm";
import { TextBlock } from "./TextBlock.js";
import { ToolUseBlock } from "./ToolUseBlock.js";
import { ToolResultBlock } from "./ToolResultBlock.js";
import { ThinkingBlock } from "./ThinkingBlock.js";
import { SkillBlock } from "./SkillBlock.js";
import { SkillResultBlock } from "./SkillResultBlock.js";
import { SkillContentBlock } from "./SkillContentBlock.js";

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

        case "skill_content":
            return html`<${SkillContentBlock} key=${key} text=${block.text} />`;

        case "tool_use":
            // Check if this is a Skill tool call - render with SkillBlock
            if (block.name === "Skill") {
                return html`
                    <${SkillBlock}
                        key=${key}
                        id=${block.id}
                        name=${block.name}
                        input=${block.input}
                    />
                `;
            }
            return html`
                <${ToolUseBlock}
                    key=${key}
                    id=${block.id}
                    name=${block.name}
                    input=${block.input}
                />
            `;

        case "tool_result":
            // Check if this is a Skill result - render with SkillResultBlock
            if (block.tool_name === "Skill") {
                return html`
                    <${SkillResultBlock}
                        key=${key}
                        tool_use_id=${block.tool_use_id}
                        tool_name=${block.tool_name}
                        content=${block.content}
                        is_error=${block.is_error}
                    />
                `;
            }
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
