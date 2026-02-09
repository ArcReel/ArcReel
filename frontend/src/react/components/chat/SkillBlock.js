import React, { useState } from "react";
import htm from "htm";
import { cn } from "../../utils.js";

const html = htm.bind(React.createElement);

function extractSkillInfo(input) {
    if (!input) return { skillName: "unknown", args: "" };

    const skillName = input.skill || input.name || "unknown";
    const args = input.args || "";
    return { skillName, args };
}

export function SkillBlock({ id, name, input }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const { skillName, args } = extractSkillInfo(input);
    const displayName = `/${skillName}`;

    return html`
        <div className="my-2 rounded-lg border border-purple-400/30 bg-purple-500/10 overflow-hidden">
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/15 transition-colors"
            >
                <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-semibold text-purple-400 uppercase shrink-0">
                        Skill
                    </span>
                    <span className="text-sm font-medium text-purple-300">
                        ${displayName}
                    </span>
                    ${args && html`
                        <span className="text-xs text-slate-400 truncate">
                            ${args.length > 40 ? args.slice(0, 40) + "..." : args}
                        </span>
                    `}
                </div>
                <span className="text-xs text-slate-500 shrink-0 ml-2">
                    ${isExpanded ? "▼" : "▶"}
                </span>
            </button>
            ${isExpanded && html`
                <div className="px-3 py-2 border-t border-purple-400/20 bg-purple-900/20">
                    <div className="text-xs text-slate-400 mb-1">调用参数</div>
                    <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
                        ${JSON.stringify(input, null, 2)}
                    </pre>
                </div>
            `}
        </div>
    `;
}
