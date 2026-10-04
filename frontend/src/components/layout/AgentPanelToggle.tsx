import { useId } from "react";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Toggle } from "@/components/ui/toggle";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { AGENT_PANEL_ID, AGENT_PANEL_TOGGLE_ID } from "./workspace-layout";

/** Agent 需要关注的状态：等待回答优先于运行中。 */
function useAgentAttention(): "waiting" | "running" | null {
  const waiting = useAssistantStore((s) => s.pendingQuestion !== null);
  const running = useAssistantStore((s) => s.sessionStatus === "running");
  if (waiting) return "waiting";
  if (running) return "running";
  return null;
}

/**
 * 顶栏右端常驻的「Agent」开关：按下态表示面板展开。面板收起时，开关上的状态点提示
 * Agent 正在运行（呼吸点）或在等你回答（琥珀点）。演示工作台的面板不可收起，不显示开关。
 */
export function AgentPanelToggle() {
  const { t } = useTranslation("dashboard");
  const open = useAppStore((s) => s.assistantPanelOpen);
  const toggle = useAppStore((s) => s.toggleAssistantPanel);
  const attention = useAgentAttention();
  const statusId = useId();
  const demoMode = useDemoWorkbench();
  if (demoMode) return null;

  const status = open ? null : attention;
  const statusText =
    status === "waiting" ? t("agent_status_waiting_answer") : status === "running" ? t("agent_status_running") : null;

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Toggle
            id={AGENT_PANEL_TOGGLE_ID}
            size="sm"
            pressed={open}
            onPressedChange={toggle}
            aria-controls={AGENT_PANEL_ID}
            aria-describedby={statusText ? statusId : undefined}
          />
        }
      >
        <Bot data-icon="inline-start" aria-hidden />
        {t("agent_panel_toggle")}
        {status && (
          <span
            aria-hidden
            className={cn("size-2 rounded-full", status === "waiting" ? "bg-warn" : "animate-breath bg-primary")}
          />
        )}
        {/* 状态只作为开关的描述朗读，不并入「Agent」这个名称：aria-describedby 可以引用 hidden 的元素 */}
        {statusText && (
          <span id={statusId} hidden>
            {statusText}
          </span>
        )}
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {statusText ?? (open ? t("collapse_panel") : t("open_assistant_panel"))}
      </TooltipContent>
    </Tooltip>
  );
}
