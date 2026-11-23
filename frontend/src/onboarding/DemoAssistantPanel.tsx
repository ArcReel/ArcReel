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

export function DemoAssistantPanel() {
  const { t } = useTranslation(["onboarding", "dashboard"]);

  const work = useMemo(() => {
    const names = (kind: "character" | "scene") => [1, 2, 3].map((n) => t(`onboarding:demo_${kind}_${n}_name`));
    return [
      { key: "characters", ...toolLabel(DEMO_TOOL, { type: "character", names: names("character") }, t, []) },
      { key: "scenes", ...toolLabel(DEMO_TOOL, { type: "scene", names: names("scene") }, t, []) },
    ];
  }, [t]);

  return (
    <div data-onboarding={ONBOARDING_ANCHORS.workbenchAgent} className="relative isolate flex h-full flex-col">
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
