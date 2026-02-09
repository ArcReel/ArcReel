import React, { useState } from "react";
import htm from "htm";
import { StreamMarkdown } from "../stream-markdown.js";

const html = htm.bind(React.createElement);

function extractSkillName(text) {
    if (!text) return "Skill";

    // Try to extract from "Launching skill: xxx"
    const launchMatch = text.match(/Launching skill:\s*(\S+)/);
    if (launchMatch) return launchMatch[1];

    // Try to extract from path
    const pathMatch = text.match(/\.claude\/skills\/([^/\s]+)/);
    if (pathMatch) return pathMatch[1];

    return "Skill";
}

export function SkillContentBlock({ text }) {
    const [isExpanded, setIsExpanded] = useState(false);

    if (!text) {
        return null;
    }

    const skillName = extractSkillName(text);

    return html`
        <div className="my-2 rounded-lg border border-purple-400/20 bg-purple-500/5 overflow-hidden">
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/10 transition-colors"
            >
                <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-purple-400">
                        Skill 内容
                    </span>
                    <span className="text-xs text-slate-400">
                        ${skillName}
                    </span>
                </div>
                <span className="text-xs text-slate-500">
                    ${isExpanded ? "▼ 收起" : "▶ 展开"}
                </span>
            </button>
            ${isExpanded && html`
                <div className="px-3 py-2 border-t border-purple-400/10 bg-purple-900/10 max-h-96 overflow-y-auto">
                    <${StreamMarkdown} content=${text} />
                </div>
            `}
        </div>
    `;
}
