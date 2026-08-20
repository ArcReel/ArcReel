import { useMemo } from "react";
import { Bot, Send } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Turn } from "@/types";
import { ChatMessage } from "@/components/copilot/chat/ChatMessage";
import { ONBOARDING_ANCHORS } from "./anchors";

/**
 * 演示工作台的助手面板。
 *
 * 真实助手面板（`AgentCopilot`）从头到尾都是写路径——建会话、SSE 订阅、跑工具，演示态
 * 一概不接。但 Agent 全程参与制作是产品核心，演示工作台不能没有它：这里用同一套消息气泡
 * （`ChatMessage`）渲染三条静态演示对话，把首次制作的时序演出来——Agent 汇报小说分析
 * 完成 → 用户发「开始制作」→ Agent 汇报推进。Agent 的每条消息都是对上一步动作的回应，
 * 不伪造工具调用卡片，输入框禁用并标「演示中不可用」。
 *
 * 引导第 8 步的 `workbench-agent` 锚点挂在这里（真实面板是写路径、演示态不挂载，锚点
 * 只对演示面板成立，见 `anchors.ts`）。
 */
export function DemoAssistantPanel() {
  const { t } = useTranslation(["onboarding", "dashboard"]);

  // 消息形状与真实面板一致（`Turn`），文案随界面语言重建。
  const turns = useMemo<Turn[]>(
    () => [
      {
        type: "assistant",
        uuid: "demo-chat-1",
        content: [{ type: "text", text: t("onboarding:demo_chat_agent_analyzed") }],
      },
      {
        type: "user",
        uuid: "demo-chat-2",
        content: [{ type: "text", text: t("onboarding:demo_chat_user_start") }],
      },
      {
        type: "assistant",
        uuid: "demo-chat-3",
        content: [{ type: "text", text: t("onboarding:demo_chat_agent_progress") }],
      },
    ],
    [t],
  );

  return (
    <div
      data-onboarding={ONBOARDING_ANCHORS.workbenchAgent}
      className="relative isolate flex h-full flex-col"
      style={{ background: "oklch(0.19 0.011 250 / 0.5)" }}
    >
      {/* 头部：与真实面板同款标识，不带会话切换/新建——演示里没有会话可管理 */}
      <div
        className="flex h-12 items-center gap-2 px-3"
        style={{ borderBottom: "1px solid var(--color-hairline)" }}
      >
        <div
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md"
          style={{
            background:
              "linear-gradient(135deg, var(--color-accent), oklch(0.60 0.10 280))",
            color: "oklch(0.12 0 0)",
          }}
        >
          <Bot className="h-3.5 w-3.5" />
        </div>
        <span className="display-serif min-w-0 truncate text-[13px] font-semibold leading-[1.1]">
          {t("dashboard:arcreel_agent")}
        </span>
      </div>

      {/* 静态演示对话 */}
      <div className="flex-1 min-w-0 space-y-3 overflow-y-auto overflow-x-hidden px-3 py-3">
        {turns.map((turn) => (
          <ChatMessage key={turn.uuid} message={turn} />
        ))}
      </div>

      {/* 输入区：只作形态展示，演示中不可用 */}
      <div
        className="p-3"
        style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
      >
        <div
          className="flex items-end gap-2 rounded-lg px-3 py-2"
          style={{
            border: "1px solid var(--color-hairline)",
            background: "oklch(0.20 0.012 265 / 0.7)",
          }}
        >
          <textarea
            rows={1}
            disabled
            placeholder={t("onboarding:demo_action_unavailable")}
            aria-label={t("dashboard:assistant_input")}
            className="flex-1 resize-none overflow-hidden bg-transparent text-[13px] outline-none disabled:cursor-not-allowed"
            style={{ color: "var(--color-text)" }}
          />
          <button
            type="button"
            disabled
            className="shrink-0 rounded-md p-1.5 disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              color: "oklch(0.14 0 0)",
              background:
                "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
            }}
            title={t("onboarding:demo_action_unavailable")}
            aria-label={t("dashboard:send_message")}
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
