import React, { useState } from "react";
import htm from "htm";
import { cn } from "../../utils.js";
import { StreamMarkdown } from "../stream-markdown.js";

const html = htm.bind(React.createElement);

/**
 * Get a summary of the tool input for display.
 */
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

/**
 * Extract skill name and args from Skill tool input.
 */
function extractSkillInfo(input) {
    if (!input) return { skillName: "unknown", args: "" };
    return {
        skillName: input.skill || input.name || "unknown",
        args: input.args || "",
    };
}

/**
 * ToolCallWithResult - Unified display of tool_use with its result.
 *
 * For regular tools: collapsible with input/result
 * For Skill tool: special styling with skill content
 */
export function ToolCallWithResult({ block }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const toolName = block.name || "Tool";
    const isSkill = toolName === "Skill";
    const hasResult = block.result !== undefined;
    const hasSkillContent = !!block.skill_content;
    const isError = block.is_error;

    // Determine colors based on state
    const borderClass = isError
        ? "border-red-500/30"
        : isSkill
            ? "border-purple-400/30"
            : "border-white/15";

    const bgClass = isError
        ? "bg-red-500/5"
        : isSkill
            ? "bg-purple-500/10"
            : "bg-ink-800/50";

    const labelColor = isError
        ? "text-red-400"
        : isSkill
            ? "text-purple-400"
            : "text-amber-400";

    // Status indicator
    const statusIcon = hasResult
        ? isError
            ? "✗"
            : "✓"
        : "…";

    const statusColor = hasResult
        ? isError
            ? "text-red-400"
            : "text-emerald-400"
        : "text-slate-500";

    // Summary text
    const summary = isSkill
        ? `/${extractSkillInfo(block.input).skillName}`
        : getToolSummary(toolName, block.input);

    const args = isSkill ? extractSkillInfo(block.input).args : null;

    return html`
        <div className=${cn("my-1.5 rounded-lg border overflow-hidden", borderClass, bgClass)}>
            <button
                type="button"
                onClick=${() => setIsExpanded(!isExpanded)}
                className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-white/5 transition-colors"
            >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                    <span className=${cn("text-xs font-semibold uppercase shrink-0", labelColor)}>
                        ${toolName}
                    </span>
                    <span className="text-xs text-slate-300 truncate">
                        ${summary}
                    </span>
                    ${args && html`
                        <span className="text-xs text-slate-500 truncate">
                            ${args.length > 30 ? args.slice(0, 30) + "..." : args}
                        </span>
                    `}
                </div>
                <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className=${cn("text-xs font-medium", statusColor)}>
                        ${statusIcon}
                    </span>
                    <span className="text-xs text-slate-500">
                        ${isExpanded ? "▼" : "▶"}
                    </span>
                </div>
            </button>

            ${isExpanded && html`
                <div className="border-t border-white/10">
                    ${/* Tool Input Section */""}
                    <div className="px-3 py-2 bg-ink-900/30">
                        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                            输入参数
                        </div>
                        <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-32 overflow-y-auto">
                            ${JSON.stringify(block.input, null, 2)}
                        </pre>
                    </div>

                    ${/* Skill Content Section (if Skill tool) */""}
                    ${hasSkillContent && html`
                        <div className="px-3 py-2 border-t border-purple-400/10 bg-purple-900/10">
                            <div className="text-[10px] uppercase tracking-wide text-purple-400 mb-1">
                                Skill 内容
                            </div>
                            <div className="max-h-48 overflow-y-auto text-xs">
                                <${StreamMarkdown} content=${block.skill_content} />
                            </div>
                        </div>
                    `}

                    ${/* Tool Result Section */""}
                    ${hasResult && html`
                        <div className=${cn(
                            "px-3 py-2 border-t",
                            isError
                                ? "border-red-400/20 bg-red-900/10"
                                : "border-white/10 bg-ink-900/50"
                        )}>
                            <div className=${cn(
                                "text-[10px] uppercase tracking-wide mb-1",
                                isError ? "text-red-400" : "text-slate-500"
                            )}>
                                ${isError ? "执行失败" : "执行结果"}
                            </div>
                            <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
                                ${typeof block.result === "string"
                                    ? block.result
                                    : JSON.stringify(block.result, null, 2)}
                            </pre>
                        </div>
                    `}
                </div>
            `}
        </div>
    `;
}
