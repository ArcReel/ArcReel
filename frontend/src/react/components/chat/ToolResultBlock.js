import React, { useState } from "react";
import htm from "htm";
import { cn } from "../../utils.js";

const html = htm.bind(React.createElement);

export function ToolResultBlock({ tool_use_id, content, is_error }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const displayContent = content || "";
    const isLong = displayContent.length > 200;
    const previewContent = isLong ? displayContent.slice(0, 200) + "..." : displayContent;

    const borderClass = is_error
        ? "border-red-500/30 bg-red-500/5"
        : "border-white/10 bg-ink-800/30";

    return html`
        <div className=${cn("my-2 rounded-lg border overflow-hidden", borderClass)}>
            <div className="px-3 py-2">
                <div className="flex items-center justify-between mb-1">
                    <span className=${cn(
                        "text-xs font-medium",
                        is_error ? "text-red-400" : "text-slate-400"
                    )}>
                        ${is_error ? "执行失败" : "执行结果"}
                    </span>
                    ${isLong && html`
                        <button
                            type="button"
                            onClick=${() => setIsExpanded(!isExpanded)}
                            className="text-xs text-slate-500 hover:text-slate-300"
                        >
                            ${isExpanded ? "收起" : "展开全部"}
                        </button>
                    `}
                </div>
                <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
                    ${isExpanded ? displayContent : previewContent}
                </pre>
            </div>
        </div>
    `;
}
