import { useState } from "react";
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
export function ToolCallWithResult({ block }: ToolCallWithResultProps) {
  const [isExpanded, setIsExpanded] = useState(false);

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
  const containerStyle: React.CSSProperties = isError
    ? {
        border: "1px solid oklch(0.70 0.18 25 / 0.3)",
        background: "oklch(0.70 0.18 25 / 0.06)",
      }
    : isSkill
      ? {
          border: "1px solid var(--color-accent-soft)",
          background: "var(--color-accent-dim)",
        }
      : {
          border: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.21 0.012 265 / 0.5)",
        };

  const labelColor = isError
    ? "var(--color-danger)"
    : isSkill
      ? "var(--color-accent-2)"
      : "var(--color-warn)";

  // -- status indicator ------------------------------------------------------
  const statusIcon = hasResult ? (isError ? "\u2717" : "\u2713") : "\u2026";

  const statusColor = hasResult
    ? isError
      ? "var(--color-danger)"
      : "var(--color-good)"
    : "var(--color-text-4)";

  // -- summary text ----------------------------------------------------------
  const summary = isSkill
    ? `/${extractSkillInfo(block.input).skillName}`
    : getToolSummary(toolName, block.input);

  return (
    <div
      className="my-1.5 min-w-0 overflow-hidden rounded-lg"
      style={containerStyle}
    >
      {/* Header button */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors"
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(1 0 0 / 0.04)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
          <span
            className="shrink-0 text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: labelColor }}
          >
            {toolName}
          </span>
          <span
            className="num truncate text-[11px]"
            style={{ color: "var(--color-text-2)" }}
          >
            {summary}
          </span>
        </div>
        <div className="ml-1.5 flex shrink-0 items-center gap-1.5">
          <span
            className="text-xs font-medium"
            style={{ color: statusColor }}
          >
            {statusIcon}
          </span>
          <span
            className="text-[10px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {isExpanded ? "\u25BC" : "\u25B6"}
          </span>
        </div>
      </button>

      {/* Expandable detail sections */}
      {isExpanded && (
        <div style={{ borderTop: "1px solid var(--color-hairline-soft)" }}>
          {/* Tool Input */}
          <div
            className="px-2.5 py-2"
            style={{ background: "oklch(0.16 0.010 265 / 0.5)" }}
          >
            <div
              className="mb-1 text-[10px] uppercase tracking-wide"
              style={{ color: "var(--color-text-4)" }}
            >
              输入参数
            </div>
            <pre
              className="num max-h-32 overflow-y-auto whitespace-pre-wrap break-all text-[11px]"
              style={{ color: "var(--color-text-2)" }}
            >
              {JSON.stringify(block.input, null, 2)}
            </pre>
          </div>

          {/* Skill Content (only for Skill tool) */}
          {hasSkillContent && (
            <div
              className="px-2.5 py-2"
              style={{
                borderTop: "1px solid var(--color-accent-soft)",
                background: "var(--color-accent-dim)",
              }}
            >
              <div
                className="mb-1 text-[10px] uppercase tracking-wide"
                style={{ color: "var(--color-accent-2)" }}
              >
                Skill 内容
              </div>
              <div className="max-h-48 overflow-hidden overflow-y-auto text-xs">
                <StreamMarkdown content={block.skill_content!} />
              </div>
            </div>
          )}

          {/* Tool Result */}
          {hasResult && (
            <div
              className="px-2.5 py-2"
              style={{
                borderTop: isError
                  ? "1px solid oklch(0.70 0.18 25 / 0.25)"
                  : "1px solid var(--color-hairline-soft)",
                background: isError
                  ? "oklch(0.70 0.18 25 / 0.08)"
                  : "oklch(0.16 0.010 265 / 0.5)",
              }}
            >
              <div
                className="mb-1 text-[10px] uppercase tracking-wide"
                style={{
                  color: isError ? "var(--color-danger)" : "var(--color-text-4)",
                }}
              >
                {isError ? "执行失败" : "执行结果"}
              </div>
              <pre
                className="num max-h-48 overflow-y-auto whitespace-pre-wrap break-all text-[11px]"
                style={{ color: "var(--color-text-2)" }}
              >
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
  const statusColor = hasResult ? "var(--color-good)" : "var(--color-text-4)";

  return (
    <div
      className="my-1.5 min-w-0 overflow-hidden rounded-lg"
      style={{
        border: "1px solid var(--color-hairline-soft)",
        background: "oklch(0.21 0.012 265 / 0.5)",
      }}
    >
      <div className="flex items-center justify-between px-2.5 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
          <span
            className="shrink-0 text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: "var(--color-text-4)" }}
          >
            TodoWrite
          </span>
          <span
            className="truncate text-[11px]"
            style={{ color: "var(--color-text-2)" }}
          >
            {total > 0 ? `任务清单 ${completed}/${total} 完成` : "任务清单已更新"}
          </span>
        </div>
        <span
          className="ml-1.5 shrink-0 text-xs font-medium"
          style={{ color: statusColor }}
        >
          {statusIcon}
        </span>
      </div>
    </div>
  );
}
