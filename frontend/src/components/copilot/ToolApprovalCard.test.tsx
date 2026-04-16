import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PendingApproval } from "@/types";
import { ToolApprovalCard } from "./ToolApprovalCard";

function makePendingApproval(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    type: "tool_approval_request",
    request_id: "ta_123",
    tool_name: "Bash",
    input: { command: "rm -rf /" },
    session_id: "session-123",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("ToolApprovalCard", () => {
  it("renders pending approval card with tool info and input", () => {
    render(
      <ToolApprovalCard
        pendingApproval={makePendingApproval({ tool_name: "Write", input: { file: "test.txt", content: "hello" } })}
        decidingApproval={false}
        onDecide={vi.fn()}
      />
    );

    expect(screen.getByText("工具名称:")).toBeInTheDocument();
    expect(screen.getByText("Write")).toBeInTheDocument();
    expect(screen.getByText(/test\.txt/)).toBeInTheDocument();
  });

  it("calls onDecide with allow when allowed", () => {
    const onDecide = vi.fn();
    render(
      <ToolApprovalCard
        pendingApproval={makePendingApproval({ request_id: "ta_allow_1" })}
        decidingApproval={false}
        onDecide={onDecide}
      />
    );

    const allowBtn = screen.getByRole("button", { name: "允许 (Allow)" });
    fireEvent.click(allowBtn);

    expect(onDecide).toHaveBeenCalledWith("ta_allow_1", "allow");
  });

  it("calls onDecide with deny and a reason when denied", () => {
    const onDecide = vi.fn();
    render(
      <ToolApprovalCard
        pendingApproval={makePendingApproval({ request_id: "ta_deny_1" })}
        decidingApproval={false}
        onDecide={onDecide}
      />
    );

    const denyBtn = screen.getByRole("button", { name: "拒绝 (Deny)" });
    fireEvent.click(denyBtn);

    expect(onDecide).toHaveBeenCalledWith("ta_deny_1", "deny", undefined, "User denied tool execution.");
  });

  it("disables buttons while deciding is true", () => {
    render(
      <ToolApprovalCard
        pendingApproval={makePendingApproval()}
        decidingApproval={true}
        onDecide={vi.fn()}
      />
    );

    const allowBtn = screen.getByRole("button", { name: "处理中..." });
    const denyBtn = screen.getByRole("button", { name: "处理中..." }); // using text content because it changes
    // Actually there are 2 buttons with the same inner text "处理中..." right now, let's grab by index or class
    
    // Using getAllByRole
    const btns = screen.getAllByRole("button");
    btns.forEach(btn => {
      expect(btn).toBeDisabled();
    });
  });
});
