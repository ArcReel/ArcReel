/**
 * 演示 Agent 面板演的是首次制作的时序，先后顺序是内容的一部分：用户发「开始制作」→ 已完成的
 * 工序 → Agent 汇报推进。顺序反了，引导第 7 步讲的流程就对不上，而这既不会让 typecheck 报错，
 * 也不会被锚点测试发现。
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { DemoAssistantPanel } from "./DemoAssistantPanel";

const t = i18n.getFixedT(null, ["onboarding", "dashboard"]);

describe("DemoAssistantPanel", () => {
  it("plays the first production run in order: the request, the finished work, then the agent's report", () => {
    render(<DemoAssistantPanel />);

    const nodes = [
      screen.getByText(t("onboarding:demo_chat_user_start")),
      screen.getByText(/爱丽丝、白兔、柴郡猫/),
      screen.getByText(/河岸柳树下、兔子洞长廊、疯茶会花园/),
      screen.getByText(t("onboarding:demo_chat_agent_progress")),
    ];
    for (let i = 1; i < nodes.length; i++) {
      expect(nodes[i - 1]?.compareDocumentPosition(nodes[i] as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    }
  });

  it("shows the finished work as plain rows that cannot be expanded", () => {
    render(<DemoAssistantPanel />);

    // 工序行没有折叠触发器，也就不带参数与结果
    expect(screen.queryByRole("button", { name: /生成资产/ })).not.toBeInTheDocument();
    expect(screen.getAllByText(t("dashboard:tool_name_generate_assets"))).toHaveLength(2);
  });

  it("keeps the input disabled — the demo never writes", () => {
    render(<DemoAssistantPanel />);

    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: t("dashboard:send_message") })).toBeDisabled();
  });
});
