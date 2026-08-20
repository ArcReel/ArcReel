/**
 * 演示助手面板演的是首次制作的时序，条数与角色顺序都是内容的一部分：Agent 汇报分析
 * 完成 → 用户发「开始制作」→ Agent 汇报推进。少一条或顺序反了，引导第 8 步就讲不出
 * 完整流程，而这既不会让 typecheck 报错，也不会被锚点测试发现。
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { DemoAssistantPanel } from "./DemoAssistantPanel";

const t = i18n.getFixedT(null, "onboarding");

describe("DemoAssistantPanel", () => {
  it("renders the three demo messages in production order", () => {
    render(<DemoAssistantPanel />);

    const texts = [
      t("demo_chat_agent_analyzed"),
      t("demo_chat_user_start"),
      t("demo_chat_agent_progress"),
    ];
    for (const text of texts) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }

    // 顺序：三条消息在 DOM 里的先后必须与制作时序一致。
    const nodes = texts.map((text) => screen.getByText(text));
    for (let i = 1; i < nodes.length; i++) {
      const previous = nodes[i - 1];
      const current = nodes[i];
      expect(previous && current && previous.compareDocumentPosition(current)).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }
  });

  it("keeps the input disabled — the demo never writes", () => {
    render(<DemoAssistantPanel />);

    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
