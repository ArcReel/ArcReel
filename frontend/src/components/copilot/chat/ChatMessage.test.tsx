import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Turn } from "@/types";
import { ChatMessage } from "./ChatMessage";

describe("ChatMessage", () => {
  it("renders a typed Agent failure as the dedicated card instead of assistant prose", () => {
    const message: Turn = {
      type: "system",
      subtype: "agent_turn_failure",
      content: [{
        type: "agent_failure",
        failure: {
          version: 1,
          phase: "turn",
          timestamp: "2026-07-23T01:02:03Z",
          project_name: "demo",
          session_id: "session-1",
          summary: {
            source: "sdk_assistant",
            type: "invalid_request",
            message: "raw upstream message",
          },
          raw: { assistant_message: { vendor_field: "keep-me" } },
        },
      }],
    };

    render(<ChatMessage message={message} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Agent 本轮运行失败");
    expect(screen.getByText("raw upstream message")).toBeInTheDocument();
    expect(screen.queryByText("系统")).not.toBeInTheDocument();
  });
});
