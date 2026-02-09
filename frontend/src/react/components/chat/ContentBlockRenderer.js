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

/**
 * Check if a tool_result content looks like a Skill result.
 * Skill results typically start with "Launching skill:" or contain skill-related content.
 */
function isSkillResultContent(content) {
    if (!content || typeof content !== "string") return false;
    const trimmed = content.trim();
    return trimmed.startsWith("Launching skill:") ||
           trimmed.includes("Skill 内容") ||
           trimmed.includes(".claude/skills/");
}

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

        case "tool_result": {
            // Check if this is a Skill result - by tool_name or by content pattern
            const isSkillResult = block.tool_name === "Skill" ||
                                  isSkillResultContent(block.content);
            if (isSkillResult) {
                return html`
                    <${SkillResultBlock}
                        key=${key}
                        tool_use_id=${block.tool_use_id}
                        tool_name=${block.tool_name || "Skill"}
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
        }

        case "thinking":
            return html`<${ThinkingBlock} key=${key} thinking=${block.thinking} />`;

        default:
            // Fallback: render as text
            const text = block.text || block.content || JSON.stringify(block);
            return html`<${TextBlock} key=${key} text=${text} />`;
    }
}
