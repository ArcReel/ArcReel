import { useMemo } from "react";
import { ArrowUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupTextarea } from "@/components/ui/input-group";
import { Message, MessageContent } from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { TextBlock } from "@/components/copilot/chat/TextBlock";
import { WorkRow } from "@/components/copilot/chat/WorkRow";
import { toolLabel } from "@/components/copilot/chat/work-label";
import { ONBOARDING_ANCHORS } from "./anchors";

const DEMO_TOOL = "mcp__arcreel__generate_assets";

/**
 * 演示工作台的 Agent 面板。
 *
 * 真实 Agent 面板（`AgentCopilot`）从头到尾都是写路径：建会话、SSE 订阅、跑工具，演示态一概不接。
 * 这里用消息区的同一套原语演出首次制作的时序：用户发「开始制作」→ 两条已完成的工序 → Agent 汇报
 * 推进。工序行的名称与摘要由真实会话同一个 `toolLabel` 生成，只是不可展开，不带参数与结果；
 * 输入框禁用并写「演示中不可用」。
 *
 * 引导第 7 步的 `workbench-agent` 锚点挂在根节点上，它占满 Agent 面板（见 `anchors.ts`）。
 */
export function DemoAssistantPanel() {
  const { t } = useTranslation(["onboarding", "dashboard"]);

  // 工序摘要列出演示项目里的资产名，随界面语言重建。
  const work = useMemo(() => {
    const names = (kind: "character" | "scene") => [1, 2, 3].map((n) => t(`onboarding:demo_${kind}_${n}_name`));
    return [
      { key: "characters", ...toolLabel(DEMO_TOOL, { type: "character", names: names("character") }, t, []) },
      { key: "scenes", ...toolLabel(DEMO_TOOL, { type: "scene", names: names("scene") }, t, []) },
    ];
  }, [t]);

  return (
    <div data-onboarding={ONBOARDING_ANCHORS.workbenchAgent} className="relative isolate flex h-full flex-col">
      {/* 头部：真实面板在这里显示会话标题与历史、新建入口；演示里没有会话可管理，只写面板名 */}
      <header className="flex h-12 shrink-0 items-center border-b border-border px-3">
        <h2 className="min-w-0 truncate text-sm font-medium">{t("dashboard:arcreel_agent")}</h2>
      </header>

      <MessageScrollerProvider defaultScrollPosition="end">
        <MessageScroller className="min-h-0 flex-1">
          <MessageScrollerViewport aria-label={t("dashboard:chat_transcript_label")}>
            <MessageScrollerContent>
              <MessageScrollerItem messageId="demo-user">
                <Message align="end">
                  <MessageContent>
                    <Bubble variant="tinted" align="end" className="max-w-[85%]">
                      <BubbleContent>
                        <p className="whitespace-pre-wrap">{t("onboarding:demo_chat_user_start")}</p>
                      </BubbleContent>
                    </Bubble>
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
              <MessageScrollerItem messageId="demo-reply">
                <Message align="start">
                  <MessageContent>
                    {/* 连续的工序排成没有段距的一列，与真实消息区一致 */}
                    <div className="flex min-w-0 flex-col">
                      {work.map(({ key, icon, name, summary }) => (
                        <WorkRow key={key} icon={icon} name={name} summary={summary} status="ok" />
                      ))}
                    </div>
                    <div className="max-w-[40em]">
                      <TextBlock text={t("onboarding:demo_chat_agent_progress")} />
                    </div>
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>

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
