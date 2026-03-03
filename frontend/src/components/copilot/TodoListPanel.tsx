import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Check, Circle } from "lucide-react";
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

      const input = block.input as Record<string, unknown> | undefined;
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
  const headerLabel = currentTask?.activeForm ?? "任务进行中";

  return (
    <div className="mx-3 mb-1 rounded-lg border border-white/10 bg-white/[0.03] overflow-hidden">
      {/* Header – always visible, toggles collapse */}
      <button
        type="button"
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-white/5"
      >
        {/* Chevron */}
        {collapsed ? (
          <ChevronRight className="h-3 w-3 shrink-0 text-slate-500" />
        ) : (
          <ChevronDown className="h-3 w-3 shrink-0 text-slate-500" />
        )}

        {/* Pulse dot for in_progress */}
        {currentTask && (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-400" />
          </span>
        )}

        {/* Current task label */}
        <span className="flex-1 truncate text-xs text-slate-300">
          {headerLabel}
        </span>

        {/* Progress bar + count */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="h-1 w-16 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <span className="text-[10px] tabular-nums text-slate-500">
            {completedCount}/{total}
          </span>
        </div>
      </button>

      {/* Expanded task list */}
      {!collapsed && (
        <div className="border-t border-white/5 px-3 py-1.5 space-y-0.5">
          {todos.map((todo, idx) => (
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

  return (
    <div className="flex items-center gap-2 py-0.5">
      {/* Status icon */}
      {isCompleted ? (
        <Check className="h-3 w-3 shrink-0 text-emerald-400" />
      ) : isInProgress ? (
        <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
          <span className="absolute h-2 w-2 animate-ping rounded-full bg-amber-400 opacity-40" />
          <span className="relative h-1.5 w-1.5 rounded-full bg-amber-400" />
        </span>
      ) : (
        <Circle className="h-3 w-3 shrink-0 text-slate-600" />
      )}

      {/* Label */}
      <span
        className={
          isCompleted
            ? "text-xs text-slate-500 line-through"
            : isInProgress
              ? "text-xs text-slate-200"
              : "text-xs text-slate-400"
        }
      >
        {todo.content}
      </span>
    </div>
  );
}
