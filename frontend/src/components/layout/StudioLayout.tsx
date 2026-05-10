import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { AssistantResizeHandle } from "./AssistantResizeHandle";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";
import { useTasksSSE } from "@/hooks/useTasksSSE";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useProjectsStore } from "@/stores/projects-store";
import {
  ASSISTANT_PANEL_DEFAULT_WIDTH,
  useAppStore,
} from "@/stores/app-store";
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
  const assistantPanelWidth = useAppStore((s) => s.assistantPanelWidth);
  const setAssistantPanelWidth = useAppStore((s) => s.setAssistantPanelWidth);
  const persistAssistantPanelWidth = useAppStore(
    (s) => s.persistAssistantPanelWidth,
  );

  const [isResizing, setIsResizing] = useState(false);
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(
    null,
  );
  const restoreBodyStyleRef = useRef<{ cursor: string; userSelect: string } | null>(
    null,
  );

  useTasksSSE(currentProjectName);
  useProjectEventsSSE(currentProjectName);

  const restoreBodyStyle = useCallback(() => {
    const saved = restoreBodyStyleRef.current;
    if (saved) {
      document.body.style.cursor = saved.cursor;
      document.body.style.userSelect = saved.userSelect;
      restoreBodyStyleRef.current = null;
    }
  }, []);

  const handleResizeMouseDown = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragStateRef.current = {
        startX: e.clientX,
        startWidth: assistantPanelWidth,
      };
      restoreBodyStyleRef.current = {
        cursor: document.body.style.cursor,
        userSelect: document.body.style.userSelect,
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      setIsResizing(true);
    },
    [assistantPanelWidth],
  );

  const handleResizeDoubleClick = useCallback(() => {
    setAssistantPanelWidth(ASSISTANT_PANEL_DEFAULT_WIDTH);
    persistAssistantPanelWidth();
  }, [setAssistantPanelWidth, persistAssistantPanelWidth]);

  useEffect(() => {
    if (!isResizing) return;

    const onMouseMove = (e: MouseEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      // 手柄在右侧栏左缘，鼠标向左 (clientX 减小) → 宽度增大
      const next = drag.startWidth + (drag.startX - e.clientX);
      setAssistantPanelWidth(next);
    };

    const onMouseUp = () => {
      dragStateRef.current = null;
      restoreBodyStyle();
      setIsResizing(false);
      persistAssistantPanelWidth();
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      // 组件意外卸载时兜底清理 body 样式
      restoreBodyStyle();
    };
  }, [
    isResizing,
    setAssistantPanelWidth,
    persistAssistantPanelWidth,
    restoreBodyStyle,
  ]);

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
          className={`relative shrink-0 overflow-hidden ${
            isResizing
              ? "transition-[min-width,border-color]"
              : "transition-[width,min-width,border-color] duration-300 ease-in-out"
          }`}
          style={{
            width: assistantPanelOpen ? assistantPanelWidth : 0,
            background: "oklch(0.19 0.011 250 / 0.5)",
            borderLeft: assistantPanelOpen
              ? "1px solid var(--color-hairline)"
              : "1px solid transparent",
          }}
        >
          {assistantPanelOpen ? (
            <AssistantResizeHandle
              width={assistantPanelWidth}
              isResizing={isResizing}
              onMouseDown={handleResizeMouseDown}
              onDoubleClick={handleResizeDoubleClick}
            />
          ) : null}
          {/* 始终渲染但收起时透明 + 不可达，保持内部状态；invisible + aria-hidden 防止 Tab 仍可聚焦内部控件 */}
          <div
            aria-hidden={!assistantPanelOpen}
            inert={!assistantPanelOpen}
            className={`h-full transition-opacity duration-200 ${
              assistantPanelOpen
                ? "opacity-100"
                : "pointer-events-none invisible opacity-0"
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
        disabled={assistantPanelOpen}
        tabIndex={assistantPanelOpen ? -1 : 0}
        aria-hidden={assistantPanelOpen}
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
        <Bot className="h-5 w-5" />
      </button>
    </div>
  );
}
