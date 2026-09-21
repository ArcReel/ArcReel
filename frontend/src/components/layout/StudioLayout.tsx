import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { AssistantResizeHandle } from "./AssistantResizeHandle";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";
import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { TaskFailureListener } from "./TaskFailureListener";
import { ScriptGenerationNoticeListener } from "./ScriptGenerationNoticeListener";
import { MigrationRepairBanner } from "./MigrationRepairBanner";
import { useProjectsStore } from "@/stores/projects-store";
import { DemoAssistantPanel } from "@/onboarding/DemoAssistantPanel";
import { DemoReadOnlyBanner } from "@/onboarding/DemoReadOnlyBanner";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import {
  ASSISTANT_PANEL_DEFAULT_WIDTH,
  clampAssistantPanelWidth,
  useAppStore,
} from "@/stores/app-store";
import { UI_LAYERS } from "@/utils/ui-layers";

interface StudioLayoutProps {
  children: React.ReactNode;
}

/**
 * 工作台三栏布局壳：顶栏 + （侧栏 / 主区 / Agent 面板）。
 */
export function StudioLayout({ children }: StudioLayoutProps) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  // 演示项目在后端不存在：任务 / 项目事件流和 Agent 都是真实写路径，演示态下整条都不接
  const demoMode = useDemoWorkbench();
  const assistantPanelOpen = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const assistantPanelWidth = useAppStore((s) => s.assistantPanelWidth);
  const setAssistantPanelWidth = useAppStore((s) => s.setAssistantPanelWidth);
  const persistAssistantPanelWidth = useAppStore(
    (s) => s.persistAssistantPanelWidth,
  );

  // 拖动期间的"草稿宽度"。非 null 表示正在拖动，UI 用 draftWidth 即时反馈；
  // mouseup / blur 时才把 draftWidth 提交到 store + localStorage，避免每帧
  // 触发 zustand 订阅链路。draftWidthRef 与 state 同步更新，让 finishResize
  // 能在 setState updater 之外读取最终值（updater 必须保持纯净）。
  const [draftWidth, setDraftWidth] = useState<number | null>(null);
  const draftWidthRef = useRef<number | null>(null);
  const isResizing = draftWidth !== null;
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(
    null,
  );
  const restoreBodyStyleRef = useRef<{ cursor: string; userSelect: string } | null>(
    null,
  );

  const updateDraftWidth = useCallback((next: number | null) => {
    draftWidthRef.current = next;
    setDraftWidth(next);
  }, []);

  // demoMode 演示→真实切换时先于 store 变为 false，currentProjectName 单独判一次
  // 兜住这一帧仍读到旧演示项目名的窗口，避免对不存在的演示项目建一次必然失败的 SSE 连接。
  // useTaskRefresh 的 projectName=null 语义是「不按项目过滤」而非「停用」，enabled 必须
  // 同步这一判定，否则该帧会退化成对全局任务的轮询而非真正停用。
  const isEffectivelyDemo = demoMode || isDemoProject(currentProjectName);
  const sseProjectName = isEffectivelyDemo ? null : currentProjectName;
  useTaskRefresh(sseProjectName, !isEffectivelyDemo);
  useProjectEventsSSE(sseProjectName);

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
      // 仅响应主键，避免右键/中键意外进入拖拽态
      if (e.button !== 0) return;
      e.preventDefault();
      const startWidth = useAppStore.getState().assistantPanelWidth;
      dragStateRef.current = { startX: e.clientX, startWidth };
      restoreBodyStyleRef.current = {
        cursor: document.body.style.cursor,
        userSelect: document.body.style.userSelect,
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      updateDraftWidth(startWidth);
    },
    [updateDraftWidth],
  );

  const handleResizeDoubleClick = useCallback(() => {
    setAssistantPanelWidth(ASSISTANT_PANEL_DEFAULT_WIDTH);
    persistAssistantPanelWidth();
  }, [setAssistantPanelWidth, persistAssistantPanelWidth]);

  useEffect(() => {
    if (!isResizing) return;

    const finishResize = () => {
      dragStateRef.current = null;
      restoreBodyStyle();
      const final = draftWidthRef.current;
      updateDraftWidth(null);
      if (final != null) {
        // 把 draft 提交到 store；setter 内部会再 clamp，persist 读 store 最新值
        setAssistantPanelWidth(final);
        persistAssistantPanelWidth();
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      // 主键已在中途松开（如焦点切走时）→ 主动收尾
      if ((e.buttons & 1) === 0) {
        finishResize();
        return;
      }
      // 手柄在右侧栏左缘，鼠标向左 (clientX 减小) → 宽度增大
      const next = clampAssistantPanelWidth(
        drag.startWidth + (drag.startX - e.clientX),
      );
      updateDraftWidth(next);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", finishResize);
    // 鼠标在窗外松开时 mouseup 可能不触发，blur 兜底防止卡死
    window.addEventListener("blur", finishResize);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", finishResize);
      window.removeEventListener("blur", finishResize);
      // 组件意外卸载时兜底清理 body 样式
      restoreBodyStyle();
    };
  }, [
    isResizing,
    setAssistantPanelWidth,
    persistAssistantPanelWidth,
    restoreBodyStyle,
    updateDraftWidth,
  ]);

  const displayedPanelWidth = draftWidth ?? assistantPanelWidth;

  return (
    <div
      className="flex h-screen flex-col"
      style={{ color: "var(--color-text)" }}
    >
      <TaskFailureListener projectName={sseProjectName} />
      <ScriptGenerationNoticeListener />
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      {demoMode ? <DemoReadOnlyBanner /> : null}
      <MigrationRepairBanner />
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar />
        <main className="flex-1 overflow-hidden">
          {children}
        </main>
        {/* 真实 Agent 是写路径（建会话、跑工具），演示态下换成静态演示对话的面板：
            不可收起、不可拖宽——演示里没有要腾的空间，少两个交互点。宽度封顶在默认宽度
            但随视口收缩：真实面板窄屏下还能手动收起，演示面板收不起来，引导期间底层又是
            inert 的，固定 505px 会把工作区挤没，后面几步就没东西可看了 */}
        {demoMode ? (
          <div
            className="shrink-0 overflow-hidden"
            style={{
              width: `min(${ASSISTANT_PANEL_DEFAULT_WIDTH}px, 40vw)`,
              minWidth: 0,
              background: "oklch(0.19 0.011 250 / 0.5)",
              borderLeft: "1px solid var(--color-hairline)",
            }}
          >
            <DemoAssistantPanel />
          </div>
        ) : (
        <div
          className={`relative shrink-0 overflow-hidden ${
            isResizing
              ? "transition-[min-width,border-color]"
              : "transition-[width,min-width,border-color] duration-300 ease-in-out"
          }`}
          style={{
            width: assistantPanelOpen ? displayedPanelWidth : 0,
            background: "oklch(0.19 0.011 250 / 0.5)",
            borderLeft: assistantPanelOpen
              ? "1px solid var(--color-hairline)"
              : "1px solid transparent",
          }}
        >
          {assistantPanelOpen ? (
            <AssistantResizeHandle
              width={displayedPanelWidth}
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
        )}
      </div>

      {/* 悬浮 Agent 球：收起时显示在右上角 */}
      {demoMode ? null : (
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
      )}
    </div>
  );
}
