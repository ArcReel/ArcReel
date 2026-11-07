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

/** Agent attention state: awaiting an answer takes precedence over running. */
function useAgentAttention(): "waiting" | "running" | null {
  const waiting = useAssistantStore((s) => s.pendingQuestion !== null);
  const running = useAssistantStore((s) => s.sessionStatus === "running");
  if (waiting) return "waiting";
  if (running) return "running";
  return null;
}

/**
 * Persistent Agent toggle at the header's right end: pressed means expanded. When collapsed, its status dot indicates running
 * with a pulsing dot or awaiting an answer with an amber dot. Demo-workspace panels cannot collapse and show no toggle.
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
        {/* Read status only as the toggle's description, not as part of its Agent name; aria-describedby may reference hidden elements */}
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
