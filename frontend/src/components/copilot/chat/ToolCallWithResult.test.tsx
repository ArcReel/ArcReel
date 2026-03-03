import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ContentBlock, TodoItem } from "@/types";
import { ToolCallWithResult } from "./ToolCallWithResult";

function makeTodo(
  content: string,
  status: TodoItem["status"] = "pending",
): TodoItem {
  return {
    content,
    activeForm: `正在处理${content}`,
    status,
  };
}

function makeTodoWriteBlock(overrides: Partial<ContentBlock> = {}): ContentBlock {
  return {
    type: "tool_use",
    id: "todo-write-1",
    name: "TodoWrite",
    input: {
      todos: [makeTodo("准备任务"), makeTodo("完成任务", "completed")],
    },
    ...overrides,
  };
}

describe("ToolCallWithResult", () => {
  it("keeps successful TodoWrite calls in the compact summary mode", () => {
    render(<ToolCallWithResult block={makeTodoWriteBlock({ result: "ok" })} />);

    expect(screen.getByText("任务清单 1/2 完成")).toBeInTheDocument();
    expect(screen.queryByText("执行失败")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the generic expandable error view for failed TodoWrite calls", () => {
    render(
      <ToolCallWithResult
        block={makeTodoWriteBlock({
          result: "permission denied",
          is_error: true,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("执行失败")).toBeInTheDocument();
    expect(screen.getByText("permission denied")).toBeInTheDocument();
    expect(screen.queryByText("任务清单 1/2 完成")).not.toBeInTheDocument();
  });
});
