import { useLocation } from "wouter";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";
import { useTasksSSE } from "@/hooks/useTasksSSE";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { UI_LAYERS } from "@/utils/ui-layers";

interface StudioLayoutProps {
  children: React.ReactNode;
}

/**
 * 工作台三栏布局壳：顶栏 + （侧栏 / 主区 / 助手面板）。
 */
export function StudioLayout({ children }: StudioLayoutProps) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  const assistantPanelOpen = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);

  useTasksSSE(currentProjectName);
  useProjectEventsSSE(currentProjectName);

  return (
    <div
      className="flex h-screen flex-col"
      style={{ color: "var(--color-text)" }}
    >
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar />
        <main className="flex-1 overflow-hidden">
          {children}
        </main>
        <div
          className="shrink-0 overflow-hidden transition-[width,min-width,border-color] duration-300 ease-in-out"
          style={{
            width: assistantPanelOpen ? 505 : 0,
            background: "oklch(0.19 0.011 250 / 0.5)",
            borderLeft: assistantPanelOpen
              ? "1px solid var(--color-hairline)"
              : "1px solid transparent",
          }}
        >
          {/* 始终渲染但收起时透明 + 不响应交互，保持内部状态 */}
          <div
            className={`h-full transition-opacity duration-200 ${
              assistantPanelOpen ? "opacity-100" : "pointer-events-none opacity-0"
            }`}
          >
            <AgentCopilot />
          </div>
        </div>
      </div>

      {/* 悬浮助手球：收起时显示在右上角 */}
      <button
        type="button"
        onClick={toggleAssistantPanel}
        className={`fixed right-4 top-14 grid h-10 w-10 place-items-center rounded-xl transition-all duration-300 ease-in-out ${UI_LAYERS.workspaceFloating} ${
          assistantPanelOpen
            ? "scale-0 pointer-events-none opacity-0"
            : "scale-100 cursor-pointer opacity-100"
        }`}
        style={{
          background:
            "linear-gradient(135deg, var(--color-accent), oklch(0.60 0.10 280))",
          color: "oklch(0.12 0 0)",
          boxShadow:
            "0 0 0 1px oklch(1 0 0 / 0.1), 0 6px 20px -6px var(--color-accent-glow)",
          transitionDelay: assistantPanelOpen ? "0ms" : "200ms",
        }}
        title={t("open_assistant_panel")}
        aria-label={t("open_assistant_panel")}
      >
        <Sparkles className="h-5 w-5" />
      </button>
    </div>
  );
}
