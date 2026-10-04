import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FailureObservation } from "@/types";
import { copyText } from "@/utils/clipboard";
import { AgentFailureCard } from "./AgentFailureCard";

vi.mock("@/utils/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(undefined),
}));

const turnFailure: FailureObservation = {
  version: 1,
  phase: "turn",
  timestamp: "2026-07-23T01:02:03Z",
  project_name: "demo",
  session_id: "session-1",
  summary: {
    key: "invalid_request",
    source: "sdk_assistant",
    type: "invalid_request",
    status: 403,
    message: "There's an issue with the selected model (gpt-5.6-sol).",
  },
  raw: {
    assistant_message: {
      error: "invalid_request",
      content: [{ type: "text", text: "There's an issue with the selected model (gpt-5.6-sol)." }],
      upstream_unknown: { reason_code: "vendor-17" },
    },
  },
};

describe("AgentFailureCard", () => {
  beforeEach(() => {
    vi.mocked(copyText).mockResolvedValue(undefined);
  });

  it("states a one-line conclusion and keeps the raw observation folded under Details", async () => {
    render(<AgentFailureCard failure={turnFailure} onRetry={vi.fn()} />);

    const card = screen.getByRole("alert");
    expect(card).toHaveTextContent("这一轮没有完成");
    expect(card).toHaveTextContent("模型服务拒绝了这次请求。");
    // 原始错误码与消息不在默认层
    expect(card).not.toHaveTextContent("sdk_assistant");
    expect(card).not.toHaveTextContent("403");
    expect(card).not.toHaveTextContent("gpt-5.6-sol");
    // 轮次失败不重放
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(card).toHaveTextContent("sdk_assistant");
    expect(card).toHaveTextContent("403");
    expect(card).toHaveTextContent(turnFailure.summary.message!);
    expect(screen.getByTestId("failure-observation-json")).toHaveTextContent("vendor-17");

    fireEvent.click(screen.getByRole("button", { name: "复制诊断信息" }));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(JSON.stringify(turnFailure, null, 2));
    });
    expect(await screen.findByRole("button", { name: "已复制诊断信息" })).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Agent 设置" })).toHaveAttribute(
      "href",
      "/app/settings?section=arcreel-agent",
    );
  });

  it("falls back to the phase's generic conclusion for unrecognized keys and events recorded before keys existed", () => {
    const { summary } = turnFailure;
    const { key: _key, ...withoutKey } = summary;
    const { unmount } = render(<AgentFailureCard failure={{ ...turnFailure, summary: withoutKey }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Agent 运行时出错，原始信息见「详情」。");
    unmount();

    render(<AgentFailureCard failure={{ ...turnFailure, phase: "startup", summary: { ...summary, key: "invalid_request" } }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("启动过程中出错，原始信息见「详情」。");
  });

  it("offers retry only for a startup failure whose caller supplies it", () => {
    const onRetry = vi.fn();
    render(
      <AgentFailureCard
        failure={{ ...turnFailure, phase: "startup", summary: { ...turnFailure.summary, key: "cli_not_found" } }}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Agent 没能启动");
    expect(screen.getByRole("alert")).toHaveTextContent("没有找到 Claude Code 命令行程序。");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });
});
