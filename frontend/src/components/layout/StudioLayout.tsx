import { startTransition } from "react";
import { useLocation } from "wouter";
import { Bot, Sparkles } from "lucide-react";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";
import { useTasksSSE } from "@/hooks/useTasksSSE";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { UI_LAYERS } from "@/utils/ui-layers";

// ---------------------------------------------------------------------------
// StudioLayout — three-column studio workspace shell
// ---------------------------------------------------------------------------

interface StudioLayoutProps {
  children: React.ReactNode;
}

export function StudioLayout({ children }: StudioLayoutProps) {
  const [, setLocation] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  const assistantPanelOpen = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const deferredWorkspaceFocus = useAppStore((s) => s.deferredWorkspaceFocus);
  const clearDeferredWorkspaceFocus = useAppStore((s) => s.clearDeferredWorkspaceFocus);
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);

  // 进入工作区时连接任务 SSE 流
  useTasksSSE(currentProjectName);
  useProjectEventsSSE(currentProjectName);

  const handleDeferredFocus = () => {
    if (!deferredWorkspaceFocus) return;
    const target = deferredWorkspaceFocus.target;
    clearDeferredWorkspaceFocus();
    startTransition(() => {
      setLocation(target.route);
    });
    triggerScrollTo(target);
  };

  return (
    <div className="flex h-screen flex-col bg-gray-950 text-gray-100">
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      {deferredWorkspaceFocus && (
        <div
          className={`pointer-events-none fixed top-14 left-1/2 -translate-x-1/2 ${UI_LAYERS.workspaceFloating}`}
        >
          <button
            type="button"
            onClick={handleDeferredFocus}
            className="pointer-events-auto flex items-center gap-3 rounded-2xl border border-sky-400/25 bg-gray-900/90 px-4 py-2.5 text-left shadow-[0_18px_50px_rgba(15,23,42,0.45)] backdrop-blur-md transition-all hover:-translate-y-0.5 hover:border-amber-300/30"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400/20 via-indigo-400/20 to-amber-300/20 text-sky-200">
              <Sparkles className="h-4 w-4" />
            </span>
            <span className="max-w-[28rem] text-sm text-gray-100">
              {deferredWorkspaceFocus.text}
            </span>
          </button>
        </div>
      )}
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar className="w-[15%] min-w-50 border-r border-gray-800" />
        <main className="flex-1 overflow-auto">
          {children}
        </main>
        <div
          className={`shrink-0 bg-gray-900 transition-[width,min-width,border-color] duration-300 ease-in-out overflow-hidden ${
            assistantPanelOpen ? "border-l border-gray-800" : "border-l border-transparent"
          }`}
          style={{
            width: assistantPanelOpen ? "40%" : "0",
            minWidth: assistantPanelOpen ? "22.5rem" : "0",
          }}
        >
          {/* 始终渲染但在收起时隐藏，保持状态 */}
          <div
            className={`h-full transition-opacity duration-200 ${
              assistantPanelOpen ? "opacity-100" : "opacity-0 pointer-events-none"
            }`}
          >
            <AgentCopilot />
          </div>
        </div>
      </div>

      {/* 悬浮助手球 — 收起时固定在右上角 */}
      <button
        type="button"
        onClick={toggleAssistantPanel}
        className={`fixed top-14 right-4 flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 shadow-lg shadow-indigo-500/20 transition-all duration-300 ease-in-out ${UI_LAYERS.workspaceFloating} ${
          assistantPanelOpen
            ? "scale-0 opacity-0 pointer-events-none"
            : "scale-100 opacity-100 hover:bg-indigo-500 cursor-pointer"
        }`}
        style={{ transitionDelay: assistantPanelOpen ? "0ms" : "200ms" }}
        title="展开助手面板"
        aria-label="展开助手面板"
      >
        <Bot className="h-5 w-5 text-white" />
      </button>
    </div>
  );
}
