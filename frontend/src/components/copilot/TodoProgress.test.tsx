import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { TodoItem, Turn } from "@/types";
import { TodoProgress } from "./TodoProgress";

function makeTodo(content: string, status: TodoItem["status"] = "pending"): TodoItem {
  return { content, activeForm: `正在${content}`, status };
}

function makeTodoTurn(todos: TodoItem[], overrides: Partial<Turn["content"][number]> = {}): Turn {
  return {
    type: "assistant",
    content: [{ type: "tool_use", id: "todo-1", name: "TodoWrite", input: { todos }, ...overrides }],
  };
}

describe("TodoProgress", () => {
  it("shows the current item and the done count in one row, and expands to the full list", async () => {
    const user = userEvent.setup();
    const turns = [
      makeTodoTurn([makeTodo("拆分集", "completed"), makeTodo("生成分镜", "in_progress"), makeTodo("配音")]),
    ];
    render(<TodoProgress turns={turns} draftTurn={null} />);

    const row = screen.getByRole("button", { name: /正在生成分镜.*1\/3/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "待办清单" })).not.toBeInTheDocument();

    await user.click(row);
    const list = screen.getByRole("region", { name: "待办清单" });
    expect(list).toHaveTextContent("拆分集");
    expect(list).toHaveTextContent("配音");
  });

  it("ignores failed TodoWrite updates when deriving the latest todos", () => {
    const turns = [
      makeTodoTurn([makeTodo("保留旧任务", "in_progress")]),
      makeTodoTurn([makeTodo("失败的新任务", "in_progress")], { is_error: true, result: "write failed" }),
    ];
    render(<TodoProgress turns={turns} draftTurn={null} />);

    expect(screen.getByRole("button", { name: /正在保留旧任务/ })).toBeInTheDocument();
    expect(screen.queryByText(/失败的新任务/)).not.toBeInTheDocument();
  });

  it("hides once every item is done, or when the latest update clears the list", () => {
    const { container, rerender } = render(
      <TodoProgress turns={[makeTodoTurn([makeTodo("拆分集", "completed")])]} draftTurn={null} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(<TodoProgress turns={[makeTodoTurn([makeTodo("旧任务")]), makeTodoTurn([])]} draftTurn={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
