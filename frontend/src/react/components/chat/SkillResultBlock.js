import React, { useState } from "react";
import htm from "htm";
import { cn } from "../../utils.js";
import { StreamMarkdown } from "../stream-markdown.js";

const html = htm.bind(React.createElement);

export function SkillResultBlock({ tool_use_id, tool_name, content, is_error }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const displayContent = content || "";
    const skillName = tool_name || "Skill";

    // Extract skill name from content if it starts with "Launching skill:"
    let extractedName = skillName;
    if (displayContent.startsWith("Launching skill:")) {
        const match = displayContent.match(/Launching skill:\s*(\S+)/);
        if (match) {
            extractedName = match[1];
        }
    }

    const borderClass = is_error
        ? "border-red-500/30 bg-red-500/5"
        : "border-purple-400/20 bg-purple-500/5";

    return html`
        <div className=${cn("my-2 rounded-lg border overflow-hidden", borderClass)}>
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/10 transition-colors"
            >
                <div className="flex items-center gap-2">
                    <span className=${cn(
                        "text-xs font-medium",
                        is_error ? "text-red-400" : "text-purple-400"
                    )}>
                        ${is_error ? "执行失败" : "Skill 内容"}
                    </span>
                    <span className="text-xs text-slate-400">
                        ${extractedName}
                    </span>
                </div>
                <span className="text-xs text-slate-500">
                    ${isExpanded ? "▼ 收起" : "▶ 展开"}
                </span>
            </button>
            ${isExpanded && html`
                <div className="px-3 py-2 border-t border-purple-400/10 bg-purple-900/10 max-h-96 overflow-y-auto">
                    <${StreamMarkdown} content=${displayContent} />
                </div>
            `}
        </div>
    `;
}
