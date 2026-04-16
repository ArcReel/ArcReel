import { useState } from "react";
import { cn } from "./utils";
import { StreamMarkdown } from "../StreamMarkdown";
import type { ContentBlock, TodoItem } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Produce a one-line summary of a tool call's input.
 */
function getToolSummary(name: string, input: Record<string, unknown> | undefined): string {
  if (!input) return "";

  switch (name) {
    case "Read":
      return (input.file_path as string) || "";
    case "Write":
    case "Edit":
      return (input.file_path as string) || "";
    case "Bash": {
      const cmd = (input.command as string) || "";
      return cmd.length > 60 ? cmd.slice(0, 60) + "..." : cmd;
    }
    case "Grep":
      return `"${(input.pattern as string) || ""}" in ${(input.path as string) || "."}`;
    case "Glob":
      return (input.pattern as string) || "";
    case "WebSearch":
      return (input.query as string) || "";
    case "WebFetch":
      return (input.url as string) || "";
    default: {
      const str = JSON.stringify(input);
      return str.length > 50 ? str.slice(0, 50) + "..." : str;
    }
  }
}

/**
 * Extract the skill name and arguments from a Skill tool_use input.
 */
function extractSkillInfo(input: Record<string, unknown> | undefined): {
  skillName: string;
  args: string;
} {
  if (!input) return { skillName: "unknown", args: "" };
  return {
    skillName: (input.skill as string) || (input.name as string) || "unknown",
    args: (input.args as string) || "",
  };
}

// ---------------------------------------------------------------------------
// ToolCallWithResult
// ---------------------------------------------------------------------------

interface ToolCallWithResultProps {
  block: ContentBlock;
  /** Called when user clicks the undo button on a Write/Edit tool. */
  onUndoWrite?: () => Promise<{ file_path: string } | null>;
}

/**
 * ToolCallWithResult -- unified display of a tool_use block with its
 * optional result and skill_content.
 *
 * Regular tools:  collapsible header showing tool name + summary, expandable
 *                 input / result sections.
 * Skill tool:     purple-accented header with `/skill-name`, optional skill
 *                 content rendered as markdown.
 */
export function ToolCallWithResult({ block, onUndoWrite }: ToolCallWithResultProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [undoState, setUndoState] = useState<"idle" | "pending" | "done" | "unavailable">("idle");

  const toolName = block.name || "Tool";
  const isSkill = toolName === "Skill";
  const isTodoWrite = toolName === "TodoWrite";
  const hasResult = block.result !== undefined;
  const hasSkillContent = !!block.skill_content;
  const isError = block.is_error;

  // -- TodoWrite compact display -----------------------------------------------
  if (isTodoWrite && !isError) {
    return <TodoWriteCompact block={block} />;
  }

  // -- colours ---------------------------------------------------------------
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

  // -- status indicator ------------------------------------------------------
  const statusIcon = hasResult ? (isError ? "\u2717" : "\u2713") : "\u2026";

  const statusColor = hasResult
    ? isError
      ? "text-red-400"
      : "text-emerald-400"
    : "text-slate-500";

  // -- summary text ----------------------------------------------------------
  const summary = isSkill
    ? `/${extractSkillInfo(block.input).skillName}`
    : getToolSummary(toolName, block.input);

  return (
    <div className={cn("my-1.5 rounded-lg border overflow-hidden min-w-0", borderClass, bgClass)}>
      {/* Header button */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-2.5 py-1.5 flex items-center justify-between text-left hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          <span className={cn("text-[10px] font-semibold uppercase shrink-0", labelColor)}>
            {toolName}
          </span>
          <span className="text-[11px] text-slate-300 truncate">
            {summary}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 ml-1.5">
          {/* Undo button — only for successful Write/Edit with a handler */}
          {onUndoWrite && hasResult && !isError && (toolName === "Write" || toolName === "Edit") && undoState !== "done" && (
            <button
              type="button"
              onClick={async (e) => {
                e.stopPropagation();
                setUndoState("pending");
                const result = await onUndoWrite();
                setUndoState(result ? "done" : "unavailable");
              }}
              disabled={undoState === "pending"}
              className={cn(
                "text-[9px] px-1.5 py-0.5 rounded border transition-colors",
                undoState === "unavailable"
                  ? "border-red-500/30 text-red-400/60 cursor-not-allowed"
                  : "border-slate-600 text-slate-400 hover:border-amber-400/50 hover:text-amber-300",
              )}
              title={undoState === "unavailable" ? "Undo unavailable" : "Undo this write"}
            >
              {undoState === "pending" ? "…" : undoState === "unavailable" ? "✗" : "↩"}
            </button>
          )}
          {undoState === "done" && (
            <span className="text-[9px] text-emerald-400/70">✓</span>
          )}
          <span className={cn("text-xs font-medium", statusColor)}>
            {statusIcon}
          </span>
          <span className="text-[10px] text-slate-500">
            {isExpanded ? "▼" : "▶"}
          </span>
        </div>
      </button>

      {/* Expandable detail sections */}
      {isExpanded && (
        <div className="border-t border-white/10">
          {/* Tool Input */}
          <div className="px-2.5 py-2 bg-ink-900/30">
            <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
              输入参数
            </div>
            <pre className="text-[11px] text-slate-300 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
              {JSON.stringify(block.input, null, 2)}
            </pre>
          </div>

          {/* Skill Content (only for Skill tool) */}
          {hasSkillContent && (
            <div className="px-2.5 py-2 border-t border-purple-400/10 bg-purple-900/10">
              <div className="text-[10px] uppercase tracking-wide text-purple-400 mb-1">
                Skill 内容
              </div>
              <div className="max-h-48 overflow-y-auto text-xs overflow-hidden">
                <StreamMarkdown content={block.skill_content!} />
              </div>
            </div>
          )}

          {/* Tool Result */}
          {hasResult && (
            <div
              className={cn(
                "px-2.5 py-2 border-t",
                isError
                  ? "border-red-400/20 bg-red-900/10"
                  : "border-white/10 bg-ink-900/50",
              )}
            >
              <div
                className={cn(
                  "text-[10px] uppercase tracking-wide mb-1",
                  isError ? "text-red-400" : "text-slate-500",
                )}
              >
                {isError ? "执行失败" : "执行结果"}
              </div>
              <pre className="text-[11px] text-slate-300 whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                {typeof block.result === "string"
                  ? block.result
                  : JSON.stringify(block.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TodoWriteCompact – single-line summary for TodoWrite tool calls
// ---------------------------------------------------------------------------

function TodoWriteCompact({ block }: Readonly<{ block: ContentBlock }>) {
  const input = block.input;
  const todos: TodoItem[] = Array.isArray(input?.todos) ? (input.todos as TodoItem[]) : [];
  const total = todos.length;
  const completed = todos.filter((t) => t.status === "completed").length;
  const hasResult = block.result !== undefined;
  const statusIcon = hasResult ? "\u2713" : "\u2026";
  const statusColor = hasResult ? "text-emerald-400" : "text-slate-500";

  return (
    <div className="my-1.5 rounded-lg border border-white/15 bg-ink-800/50 overflow-hidden min-w-0">
      <div className="px-2.5 py-1.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          <span className="text-[10px] font-semibold uppercase shrink-0 text-slate-500">
            TodoWrite
          </span>
          <span className="text-[11px] text-slate-300 truncate">
            {total > 0 ? `任务清单 ${completed}/${total} 完成` : "任务清单已更新"}
          </span>
        </div>
        <span className={cn("text-xs font-medium shrink-0 ml-1.5", statusColor)}>
          {statusIcon}
        </span>
      </div>
    </div>
  );
}
