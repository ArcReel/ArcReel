import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Check, Circle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ProgressBar } from "@/components/ui/ProgressBar";
import type { Turn, TodoItem } from "@/types";

// ---------------------------------------------------------------------------
// extractLatestTodos – scan turns (back-to-front) to find the most recent
// TodoWrite tool_use block and return its input.todos array.
// ---------------------------------------------------------------------------

export function extractLatestTodos(
  turns: Turn[],
  draftTurn: Turn | null,
): TodoItem[] | null {
  const allTurns = draftTurn ? [...turns, draftTurn] : turns;

  for (let i = allTurns.length - 1; i >= 0; i--) {
    const turn = allTurns[i];
    if (!Array.isArray(turn.content)) continue;
    for (let j = turn.content.length - 1; j >= 0; j--) {
      const block = turn.content[j];
      if (block.type !== "tool_use" || block.name !== "TodoWrite" || block.is_error === true) {
        continue;
      }

      const input = block.input;
      const todos = input?.todos;
      if (
        Array.isArray(todos) &&
        todos.every(
          (item: unknown) =>
            item && typeof item === "object" && "content" in item && "status" in item,
        )
      ) {
        return todos as TodoItem[];
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// TodoListPanel
// ---------------------------------------------------------------------------

interface TodoListPanelProps {
  turns: Turn[];
  draftTurn: Turn | null;
}

export function TodoListPanel({ turns, draftTurn }: TodoListPanelProps) {
  const { t } = useTranslation("dashboard");
  const [collapsed, setCollapsed] = useState(false);

  const todos = useMemo(
    () => extractLatestTodos(turns, draftTurn),
    [turns, draftTurn],
  );

  // Hide when no todos or all completed
  if (!todos || todos.length === 0) return null;
  const allCompleted = todos.every((t) => t.status === "completed");
  if (allCompleted) return null;

  const completedCount = todos.filter((t) => t.status === "completed").length;
  const total = todos.length;
  const progressPercent = Math.round((completedCount / total) * 100);
  const currentTask = todos.find((t) => t.status === "in_progress");
  const headerLabel = currentTask?.activeForm ?? t("task_in_progress_default");

  return (
    <div
      className="mx-3 mb-1 overflow-hidden rounded-lg"
      style={{
        border: "1px solid var(--color-hairline-soft)",
        background: "oklch(0.20 0.012 265 / 0.5)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
      }}
    >
      {/* Header – always visible, toggles collapse */}
      <button
        type="button"
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors"
        style={{ color: "var(--color-text-2)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.24 0.012 265 / 0.5)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        {/* Chevron */}
        {collapsed ? (
          <ChevronRight
            className="h-3 w-3 shrink-0"
            style={{ color: "var(--color-text-4)" }}
          />
        ) : (
          <ChevronDown
            className="h-3 w-3 shrink-0"
            style={{ color: "var(--color-text-4)" }}
          />
        )}

        {/* Pulse dot for in_progress */}
        {currentTask && (
          <span className="relative flex h-2 w-2 shrink-0">
            <span
              className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
              style={{ background: "var(--color-warn)" }}
            />
            <span
              className="relative inline-flex h-2 w-2 rounded-full"
              style={{ background: "var(--color-warn)" }}
            />
          </span>
        )}

        {/* Current task label */}
        <span
          className="flex-1 truncate text-[12px]"
          style={{ color: "var(--color-text-2)" }}
        >
          {headerLabel}
        </span>

        {/* Progress bar + count */}
        <div className="flex shrink-0 items-center gap-2">
          <ProgressBar
            value={progressPercent}
            className="h-1 w-16 rounded-full bg-[oklch(0.30_0.012_265_/_0.5)]"
            barClassName="bg-[var(--color-good)] transition-all duration-500 ease-out"
          />
          <span
            className="num text-[10px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {completedCount}/{total}
          </span>
        </div>
      </button>

      {/* Expanded task list */}
      {!collapsed && (
        <div
          className="space-y-0.5 px-3 py-1.5"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          {todos.map((todo) => (
            <TodoRow key={`${todo.content}-${todo.status}`} todo={todo} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TodoRow – single todo item
// ---------------------------------------------------------------------------

function TodoRow({ todo }: { todo: TodoItem }) {
  const isCompleted = todo.status === "completed";
  const isInProgress = todo.status === "in_progress";

  const labelColor = isCompleted
    ? "var(--color-text-4)"
    : isInProgress
      ? "var(--color-text)"
      : "var(--color-text-3)";

  return (
    <div className="flex items-center gap-2 py-0.5">
      {/* Status icon */}
      {isCompleted ? (
        <Check
          className="h-3 w-3 shrink-0"
          style={{ color: "var(--color-good)" }}
        />
      ) : isInProgress ? (
        <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
          <span
            className="absolute h-2 w-2 animate-ping rounded-full opacity-40"
            style={{ background: "var(--color-warn)" }}
          />
          <span
            className="relative h-1.5 w-1.5 rounded-full"
            style={{ background: "var(--color-warn)" }}
          />
        </span>
      ) : (
        <Circle
          className="h-3 w-3 shrink-0"
          style={{ color: "var(--color-text-4)" }}
        />
      )}

      {/* Label */}
      <span
        className={`text-[12px] ${isCompleted ? "line-through" : ""}`}
        style={{ color: labelColor }}
      >
        {todo.content}
      </span>
    </div>
  );
}
