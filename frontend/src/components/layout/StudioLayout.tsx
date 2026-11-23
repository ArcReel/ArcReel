import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { usePanelRef, type LayoutChangedMeta, type PanelSize } from "react-resizable-panels";
import { cn } from "cn";
import { ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";
import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useProjectsStore } from "@/stores/projects-store";
import { DemoAssistantPanel } from "@/onboarding/DemoAssistantPanel";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import {
  ASSISTANT_PANEL_DEFAULT_WIDTH,
  ASSISTANT_PANEL_MAX_WIDTH,
  ASSISTANT_PANEL_MIN_WIDTH,
  useAppStore,
} from "@/stores/app-store";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { TaskFailureListener } from "./TaskFailureListener";
import { ScriptGenerationNoticeListener } from "./ScriptGenerationNoticeListener";
import { WorkspaceResizeHandle } from "./WorkspaceResizeHandle";
import {
  AGENT_PANEL_ID,
  AGENT_PANEL_TOGGLE_ID,
  CANVAS_MIN_WIDTH,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_RAIL_WIDTH,
  persistSidebarCollapsed,
  persistSidebarWidth,
  readSidebarCollapsed,
  readSidebarWidth,
  sidebarAutoCollapseReason,
  useCompactTier,
} from "./workspace-layout";

const HANDLE_WIDTH = 1;

interface StudioLayoutProps {
  children: React.ReactNode;
}

export function StudioLayout({ children }: StudioLayoutProps) {
  const { t } = useTranslation("dashboard");
  const [location] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  const demoMode = useDemoWorkbench();

  const isEffectivelyDemo = demoMode || isDemoProject(currentProjectName);
  const sseProjectName = isEffectivelyDemo ? null : currentProjectName;
  useTaskRefresh(sseProjectName, !isEffectivelyDemo);
  useProjectEventsSSE(sseProjectName);

  const compact = useCompactTier();
  const storeOpen = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const agentWidth = useAppStore((s) => s.assistantPanelWidth);
  const agentOpen = demoMode || storeOpen;
  const overlay = compact && !demoMode;

  const collapseReason = sidebarAutoCollapseReason(location, compact);
  const [userCollapsed, setUserCollapsed] = useState(readSidebarCollapsed);
  const [tempExpanded, setTempExpanded] = useState(false);
  const resetKey = `${collapseReason}|${location}`;
  const [lastResetKey, setLastResetKey] = useState(resetKey);
  if (resetKey !== lastResetKey) {
    setLastResetKey(resetKey);
    setTempExpanded(false);
  }
  const sidebarCollapsed = collapseReason ? !tempExpanded : userCollapsed;
  const setSidebarCollapsed = useCallback(
    (collapsed: boolean) => {
      if (collapseReason) {
        setTempExpanded(!collapsed);
        return;
      }
      setUserCollapsed(collapsed);
      persistSidebarCollapsed(collapsed);
    },
    [collapseReason],
  );

  const shellRef = useRef<HTMLDivElement>(null);
  const agentFrameRef = useRef<HTMLElement>(null);
  const sidebarPanelRef = usePanelRef();
  const agentPanelRef = usePanelRef();
  const [initialSidebarWidth] = useState(readSidebarWidth);
  const sidebarWidthRef = useRef(initialSidebarWidth);
  const latest = useRef({ sidebarCollapsed, agentOpen, agentWidth });
  useLayoutEffect(() => {
    latest.current = { sidebarCollapsed, agentOpen, agentWidth };
  });

  useLayoutEffect(() => {
    const panel = sidebarPanelRef.current;
    if (!panel) return;
    if (sidebarCollapsed) panel.collapse();
    else panel.resize(sidebarWidthRef.current);
  }, [sidebarCollapsed, sidebarPanelRef]);

  useLayoutEffect(() => {
    const panel = agentPanelRef.current;
    if (!panel) return;
    if (agentOpen) panel.resize(latest.current.agentWidth);
    else panel.collapse();
  }, [agentOpen, agentPanelRef]);

  const rememberSidebarWidth = useCallback((width: number) => {
    if (width < SIDEBAR_MIN_WIDTH) return;
    sidebarWidthRef.current = width;
    persistSidebarWidth(width);
  }, []);
  const rememberAgentWidth = useCallback((width: number) => {
    if (width < ASSISTANT_PANEL_MIN_WIDTH) return;
    const store = useAppStore.getState();
    store.setAssistantPanelWidth(width);
    store.persistAssistantPanelWidth();
  }, []);

  const userResizingRef = useRef(false);
  const resizeByKeyboard = (panelRef: typeof sidebarPanelRef, width: number) => {
    const panel = panelRef.current;
    if (!panel) return;
    userResizingRef.current = true;
    try {
      panel.resize(width);
    } finally {
      userResizingRef.current = false;
    }
  };
  useEffect(() => {
    let resetTimer: ReturnType<typeof setTimeout> | undefined;
    const handleDoubleClick = () => {
      userResizingRef.current = true;
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        userResizingRef.current = false;
      });
    };
    window.addEventListener("dblclick", handleDoubleClick, true);
    return () => {
      window.removeEventListener("dblclick", handleDoubleClick, true);
      clearTimeout(resetTimer);
    };
  }, []);

  const handleLayoutChanged = (_layout: unknown, meta: LayoutChangedMeta) => {
    const sidebar = sidebarPanelRef.current;
    const agent = agentPanelRef.current;
    const current = latest.current;
    if (meta.isUserInteraction || userResizingRef.current) {
      if (sidebar) {
        const collapsed = sidebar.isCollapsed();
        if (collapsed !== current.sidebarCollapsed) setSidebarCollapsed(collapsed);
        else if (!collapsed) rememberSidebarWidth(sidebar.getSize().inPixels);
      }
      if (agent && !demoMode) {
        const closed = agent.isCollapsed();
        if (closed === current.agentOpen) toggleAssistantPanel();
        else if (!closed) rememberAgentWidth(agent.getSize().inPixels);
      }
      return;
    }
    if (sidebar && !current.sidebarCollapsed && !sidebar.isCollapsed()) {
      const sidebarWidth = sidebarWidthRef.current;
      if (Math.abs(sidebar.getSize().inPixels - sidebarWidth) > 1) sidebar.resize(sidebarWidth);
    }
    if (agent && current.agentOpen && !agent.isCollapsed()) {
      if (Math.abs(agent.getSize().inPixels - current.agentWidth) > 1) agent.resize(current.agentWidth);
    }
  };

  const handleAgentResize = (size: PanelSize) => {
    const covered = size.inPixels > 0 ? size.inPixels + HANDLE_WIDTH : 0;
    shellRef.current?.style.setProperty("--workspace-agent-width", `${covered}px`);
  };

  const restoreFocusRef = useRef(false);
  useEffect(
    () =>
      useAppStore.subscribe((state, prev) => {
        if (prev.assistantPanelOpen && !state.assistantPanelOpen) {
          restoreFocusRef.current = agentFrameRef.current?.contains(document.activeElement) ?? false;
        }
      }),
    [],
  );
  useLayoutEffect(() => {
    if (agentOpen || !restoreFocusRef.current) return;
    restoreFocusRef.current = false;
    document.getElementById(AGENT_PANEL_TOGGLE_ID)?.focus();
  }, [agentOpen]);

  useEffect(() => {
    if (!overlay || !agentOpen) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (!(event.target instanceof Node) || !agentFrameRef.current?.contains(event.target)) return;
      event.preventDefault();
      useAppStore.getState().toggleAssistantPanel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [overlay, agentOpen]);

  return (
    <div ref={shellRef} className="relative flex h-dvh flex-col overflow-hidden text-foreground">
      <TaskFailureListener projectName={sseProjectName} />
      <ScriptGenerationNoticeListener />
      <GlobalHeader />
      <ResizablePanelGroup className="min-h-0 flex-1" onLayoutChanged={handleLayoutChanged}>
        <ResizablePanel
          id="workspace-sidebar"
          panelRef={sidebarPanelRef}
          collapsible
          collapsedSize={SIDEBAR_RAIL_WIDTH}
          minSize={SIDEBAR_MIN_WIDTH}
          maxSize={SIDEBAR_MAX_WIDTH}
          defaultSize={SIDEBAR_DEFAULT_WIDTH}
          groupResizeBehavior="preserve-pixel-size"
        >
          <AssetSidebar collapsed={sidebarCollapsed} onCollapsedChange={setSidebarCollapsed} />
        </ResizablePanel>
        <WorkspaceResizeHandle
          panelRef={sidebarPanelRef}
          panelSide="before"
          onKeyboardResize={(width) => resizeByKeyboard(sidebarPanelRef, width)}
          label={t("resize_sidebar")}
          disabled={sidebarCollapsed}
        />
        <ResizablePanel
          id="workspace-canvas"
          minSize={overlay ? 0 : CANVAS_MIN_WIDTH}
          // 库在面板内层写死 overflow: auto，只能经 style 覆盖：画布各视图自己滚动；
          // 覆盖模式下画布要伸到 Agent 面板底下，不能被这一层裁掉。
          // eslint-disable-next-line shadcn/no-inline-styles -- 覆盖库写在内层的内联 overflow
          style={{ overflow: overlay ? "visible" : "hidden" }}
        >
          <main
            className={cn(
              "@container/canvas relative isolate flex h-full min-w-0 flex-col",
              overlay && agentOpen && "w-[calc(100%+var(--workspace-agent-width))]",
            )}
          >
            {children}
          </main>
        </ResizablePanel>
        <WorkspaceResizeHandle
          panelRef={agentPanelRef}
          panelSide="after"
          onKeyboardResize={(width) => resizeByKeyboard(agentPanelRef, width)}
          label={t("resize_assistant_panel")}
          disabled={!agentOpen || demoMode}
          hidden={!agentOpen}
        />
        <ResizablePanel
          id="workspace-agent"
          panelRef={agentPanelRef}
          collapsible
          collapsedSize={0}
          minSize={ASSISTANT_PANEL_MIN_WIDTH}
          maxSize={ASSISTANT_PANEL_MAX_WIDTH}
          defaultSize={ASSISTANT_PANEL_DEFAULT_WIDTH}
          groupResizeBehavior="preserve-pixel-size"
          onResize={handleAgentResize}
          // eslint-disable-next-line shadcn/no-inline-styles -- 覆盖库写在内层的内联 overflow
          style={{ overflow: "hidden" }}
        >
          <aside
            ref={agentFrameRef}
            id={AGENT_PANEL_ID}
            aria-label={t("agent_panel_label")}
            inert={!agentOpen}
            className={cn(
              "@container-size/agent relative h-full w-full min-w-80 transition-opacity duration-fast",
              overlay ? "bg-popover shadow-overlay" : "bg-card",
              !agentOpen && "invisible opacity-0",
            )}
          >
            {demoMode ? <DemoAssistantPanel /> : <AgentCopilot />}
          </aside>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}
