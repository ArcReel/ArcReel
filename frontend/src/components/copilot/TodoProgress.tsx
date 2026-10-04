import { useMemo, useState } from "react";
import { Check, ChevronDown, ChevronUp, Circle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { TodoItem, Turn } from "@/types";

// ---------------------------------------------------------------------------
// extractLatestTodos — 从后往前找最近一次成功的 TodoWrite，取它的 todos。
// 失败的写入不算数；空清单是有效的最新状态（Agent 清空了待办）。
// ---------------------------------------------------------------------------

export function extractLatestTodos(turns: Turn[], draftTurn: Turn | null): TodoItem[] | null {
  const allTurns = draftTurn ? [...turns, draftTurn] : turns;

  for (let i = allTurns.length - 1; i >= 0; i--) {
    const turn = allTurns[i];
    if (!Array.isArray(turn.content)) continue;
    for (let j = turn.content.length - 1; j >= 0; j--) {
      const block = turn.content[j];
      if (block.type !== "tool_use" || block.name !== "TodoWrite" || block.is_error === true) {
        continue;
      }
      const todos = block.input?.todos;
      if (
        Array.isArray(todos) &&
        todos.every((item: unknown) => item && typeof item === "object" && "content" in item && "status" in item)
      ) {
        return todos as TodoItem[];
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// TodoProgress — 输入框上方的一行进度：当前进行项与完成数，点开展开清单。
// 没有待办或全部完成时不显示。
// ---------------------------------------------------------------------------

interface TodoProgressProps {
  turns: Turn[];
  draftTurn: Turn | null;
}

export function TodoProgress({ turns, draftTurn }: TodoProgressProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const todos = useMemo(() => extractLatestTodos(turns, draftTurn), [turns, draftTurn]);

  if (!todos || todos.length === 0 || todos.every((todo) => todo.status === "completed")) return null;

  const done = todos.filter((todo) => todo.status === "completed").length;
  const current = todos.find((todo) => todo.status === "in_progress");

  return (
    <div className="shrink-0 border-t border-border px-2 py-1">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="w-full justify-start" />}>
          {current ? (
            <span aria-hidden className="size-1.5 shrink-0 animate-breath rounded-full bg-primary" />
          ) : (
            <Circle aria-hidden className="text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate text-left font-normal text-subtle-foreground">
            {current?.activeForm ?? t("task_in_progress_default")}
          </span>
          <span className="shrink-0 font-normal tabular-nums text-muted-foreground">
            <span className="sr-only">{t("todo_progress_done")}</span>
            {done}/{todos.length}
          </span>
          {open ? (
            <ChevronDown aria-hidden data-icon="inline-end" className="text-muted-foreground" />
          ) : (
            <ChevronUp aria-hidden data-icon="inline-end" className="text-muted-foreground" />
          )}
        </CollapsibleTrigger>
        <CollapsibleContent>
          {/* 清单很长时只在这里滚动，不把消息区挤没；里面没有可聚焦的元素，区域自身可聚焦，键盘才能滚动 */}
          <div
            role="region"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 只读的滚动区域需要键盘聚焦才能滚动
            tabIndex={0}
            aria-label={t("todo_list_label")}
            className="relative max-h-[30cqh] overflow-y-auto rounded-md px-2 py-1.5 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <ul className="flex flex-col gap-1">
              {todos.map((todo, index) => (
                <TodoRow key={`${index}-${todo.content}`} todo={todo} />
              ))}
            </ul>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function TodoRow({ todo }: { todo: TodoItem }) {
  const { t } = useTranslation("dashboard");
  const status = todo.status;
  return (
    <li className="flex items-start gap-2 text-xs leading-normal">
      <span className="grid h-[1lh] w-3.5 shrink-0 place-items-center">
        {status === "completed" ? (
          <Check aria-label={t("todo_status_completed")} className="size-3.5 text-muted-foreground" />
        ) : status === "in_progress" ? (
          <span role="img" aria-label={t("todo_status_in_progress")} className="size-1.5 animate-breath rounded-full bg-primary" />
        ) : (
          <Circle aria-label={t("todo_status_pending")} className="size-3 text-muted-foreground" />
        )}
      </span>
      <span
        className={cn(
          "min-w-0",
          status === "completed" && "text-muted-foreground line-through",
          status === "in_progress" && "text-foreground",
          status === "pending" && "text-subtle-foreground",
        )}
      >
        {status === "in_progress" ? todo.activeForm || todo.content : todo.content}
      </span>
    </li>
  );
}
