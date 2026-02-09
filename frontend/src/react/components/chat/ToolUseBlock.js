import React, { useState } from "react";
import htm from "htm";
import { cn } from "../../utils.js";

const html = htm.bind(React.createElement);

function getToolSummary(name, input) {
    if (!input) return "";

    switch (name) {
        case "Read":
            return input.file_path || "";
        case "Write":
        case "Edit":
            return input.file_path || "";
        case "Bash":
            const cmd = input.command || "";
            return cmd.length > 60 ? cmd.slice(0, 60) + "..." : cmd;
        case "Grep":
            return `"${input.pattern || ""}" in ${input.path || "."}`;
        case "Glob":
            return input.pattern || "";
        case "WebSearch":
            return input.query || "";
        case "WebFetch":
            return input.url || "";
        default:
            const str = JSON.stringify(input);
            return str.length > 50 ? str.slice(0, 50) + "..." : str;
    }
}

export function ToolUseBlock({ id, name, input }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const summary = getToolSummary(name, input);

    return html`
        <div className="my-2 rounded-lg border border-white/15 bg-ink-800/50 overflow-hidden">
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-white/5 transition-colors"
            >
                <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-semibold text-amber-400 uppercase shrink-0">
                        ${name || "Tool"}
                    </span>
                    <span className="text-xs text-slate-400 truncate">
                        ${summary}
                    </span>
                </div>
                <span className="text-xs text-slate-500 shrink-0 ml-2">
                    ${isExpanded ? "▼" : "▶"}
                </span>
            </button>
            ${isExpanded && html`
                <div className="px-3 py-2 border-t border-white/10 bg-ink-900/50">
                    <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
                        ${JSON.stringify(input, null, 2)}
                    </pre>
                </div>
            `}
        </div>
    `;
}
