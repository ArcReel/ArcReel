import { useMemo } from "react";
import { ArrowUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Turn } from "@/types";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupTextarea } from "@/components/ui/input-group";
import { MessageRow } from "@/components/copilot/chat/MessageRow";
import { ONBOARDING_ANCHORS } from "./anchors";

/**
 * 演示工作台的 Agent 面板。
 *
 * 真实 Agent 面板（`AgentCopilot`）从头到尾都是写路径——建会话、SSE 订阅、跑工具，演示态
 * 一概不接。但 Agent 全程参与制作是产品核心，演示工作台不能没有它：这里用消息区同一个
 * 消息组件（`MessageRow`）渲染三条静态演示对话，把首次制作的时序演出来——Agent 汇报小说分析
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
    <div data-onboarding={ONBOARDING_ANCHORS.workbenchAgent} className="relative isolate flex h-full flex-col">
      {/* 头部：真实面板在这里显示会话标题与历史、新建入口；演示里没有会话可管理，只写面板名 */}
      <header className="flex h-12 shrink-0 items-center border-b border-border px-3">
        <h2 className="min-w-0 truncate text-sm font-medium">{t("dashboard:arcreel_agent")}</h2>
      </header>

      {/* 静态演示对话 */}
      <div className="relative flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-4">
        {turns.map((turn) => (
          <MessageRow key={turn.uuid} turn={turn} />
        ))}
      </div>

      {/* 输入区：与真实面板同一外形，只作展示，演示中不可用 */}
      <div className="shrink-0 border-t border-border p-3">
        <InputGroup>
          <InputGroupTextarea
            rows={1}
            disabled
            placeholder={t("onboarding:demo_action_unavailable")}
            aria-label={t("dashboard:assistant_input")}
            className="min-h-9"
          />
          <InputGroupAddon align="block-end">
            <Button size="icon-sm" className="ml-auto" disabled aria-label={t("dashboard:send_message")}>
              <ArrowUp aria-hidden />
            </Button>
          </InputGroupAddon>
        </InputGroup>
      </div>
    </div>
  );
}
