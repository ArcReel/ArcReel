import React, { useState } from "react";
import htm from "htm";

const html = htm.bind(React.createElement);

export function ThinkingBlock({ thinking }) {
    const [isExpanded, setIsExpanded] = useState(false);

    if (!thinking) {
        return null;
    }

    const preview = thinking.length > 100 ? thinking.slice(0, 100) + "..." : thinking;

    return html`
        <div className="my-2 rounded-lg border border-purple-500/20 bg-purple-500/5 overflow-hidden">
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/10 transition-colors"
            >
                <span className="text-xs font-medium text-purple-400">
                    思考过程
                </span>
                <span className="text-xs text-slate-500">
                    ${isExpanded ? "▼" : "▶"}
                </span>
            </button>
            ${isExpanded && html`
                <div className="px-3 py-2 border-t border-purple-500/10">
                    <p className="text-xs text-slate-400 italic whitespace-pre-wrap">
                        ${thinking}
                    </p>
                </div>
            `}
        </div>
    `;
}
