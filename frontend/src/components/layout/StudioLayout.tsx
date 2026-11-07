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

// Resize handles occupy 1px in document flow (the separator itself).
const HANDLE_WIDTH = 1;

interface StudioLayoutProps {
  children: React.ReactNode;
}

/**
 * Project workspace shell: a full-width header above sidebar | canvas | Agent panel columns, separated by resize handles.
 *
 * - Only the shell switches modes by viewport (1280). Standard-mode Agent panels squeeze the canvas, preserving at least 480px;
 *   compact-mode Agent panels are nonmodal overlays on the right of the canvas; Esc collapses them when focused inside, and sidebars become icon rails.
 * - Sidebars automatically collapse on episode pages and episode-list views; temporary expansion resets on page changes, and leaving restores user choices.
 * - Agent panel opening and width live in `app-store`; collapsed contents remain mounted, preserving typed text and session connections.
 * - Size containers: sidebar `@container/sidebar`, canvas `@container/canvas` (width queries), Agent panel
 *   `@container-size/agent` (width/height queries; input `40cqh` is relative to panel height).
 */
export function StudioLayout({ children }: StudioLayoutProps) {
  const { t } = useTranslation("dashboard");
  const [location] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  // Demo projects do not exist on the backend: tasks/project event streams and Agent are real write paths, all disconnected in demos.
  const demoMode = useDemoWorkbench();

  // demoMode becomes false before stores during demo→real transitions; separately check currentProjectName
  // to cover frames still reading old demo names, avoiding guaranteed-failing SSE connections to nonexistent demo projects.
  // useTaskRefresh projectName=null means no project filtering, rather than disabled; enabled must
  // mirror this check, otherwise this frame polls global tasks instead of actually disabling refresh.
  const isEffectivelyDemo = demoMode || isDemoProject(currentProjectName);
  const sseProjectName = isEffectivelyDemo ? null : currentProjectName;
  useTaskRefresh(sseProjectName, !isEffectivelyDemo);
  useProjectEventsSSE(sseProjectName);

  const compact = useCompactTier();
  const storeOpen = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const agentWidth = useAppStore((s) => s.assistantPanelWidth);
  // Demo panels cannot collapse or cover the canvas: later tour steps highlight canvas elements.
  const agentOpen = demoMode || storeOpen;
  const overlay = compact && !demoMode;

  // ---- Sidebar collapse: automatic reasons default to collapsed; temporary expansion resets when reasons/pages change ----
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

  // ---- Panel sizes: state drives library opening/collapse; results of dragging and other user operations return to state ----
  const shellRef = useRef<HTMLDivElement>(null);
  const agentFrameRef = useRef<HTMLElement>(null);
  const sidebarPanelRef = usePanelRef();
  const agentPanelRef = usePanelRef();
  // Remembered sidebar widths are read only for imperative resizing, not rendering; updates do not rerender shells.
  const [initialSidebarWidth] = useState(readSidebarWidth);
  const sidebarWidthRef = useRef(initialSidebarWidth);
  const latest = useRef({ sidebarCollapsed, agentOpen, agentWidth });
  useLayoutEffect(() => {
    latest.current = { sidebarCollapsed, agentOpen, agentWidth };
  });

  // Panel defaultSize is the double-click reset width; remembered widths are applied here at mount and subsequent opening/collapse.
  useLayoutEffect(() => {
    const panel = sidebarPanelRef.current;
    if (!panel) return;
    if (sidebarCollapsed) panel.collapse();
    else panel.resize(sidebarWidthRef.current);
  }, [sidebarCollapsed, sidebarPanelRef]);

  useLayoutEffect(() => {
    const panel = agentPanelRef.current;
    if (!panel) return;
    // Opening/collapse changes widths instantly: width animation would relayout the canvas every frame.
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

  // Keyboard resizing and double-click resets use imperative library APIs; layout changes notify synchronously without marking user operations;
  // mark user resizing during adjustment so results are remembered like dragging, rather than reverting to previous widths.
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
  // The library handles double-clicks in document capture; window capture runs first.
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
      // When drag, keyboard or double-click operations collapse/expand panels, synchronize opening state and remember resulting widths.
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
    // Other changes come from viewport/mode switches: the library scales panels using previous percentages or squeezes them for canvas minimums. Expanded panels return to remembered
    // pixel widths; when they cannot fit, the library clamps within constraints, and unchanged layouts do not trigger again.
    if (sidebar && !current.sidebarCollapsed && !sidebar.isCollapsed()) {
      const sidebarWidth = sidebarWidthRef.current;
      if (Math.abs(sidebar.getSize().inPixels - sidebarWidth) > 1) sidebar.resize(sidebarWidth);
    }
    if (agent && current.agentOpen && !agent.isCollapsed()) {
      if (Math.abs(agent.getSize().inPixels - current.agentWidth) > 1) agent.resize(current.agentWidth);
    }
  };

  // Overlay-mode canvases lay out at canvas-column + Agent-panel width, covered by panels instead of squeezed.
  const handleAgentResize = (size: PanelSize) => {
    const covered = size.inPixels > 0 ? size.inPixels + HANDLE_WIDTH : 0;
    shellRef.current?.style.setProperty("--workspace-agent-width", `${covered}px`);
  };

  // Collapsing focused panels returns focus to header Agent toggles, rather than the page beginning.
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

  // Compact overlays are nonmodal: Esc collapses when focused inside. Listen in document bubble so panel controls handle their own Esc first.
  useEffect(() => {
    if (!overlay || !agentOpen) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Only recognize keys inside panel DOM; dialogs opened there render through Portals, and Esc closes those dialogs.
      if (!(event.target instanceof Node) || !agentFrameRef.current?.contains(event.target)) return;
      event.preventDefault();
      useAppStore.getState().toggleAssistantPanel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [overlay, agentOpen]);

  return (
    // Shell roots are positioned; documents do not scroll. Scrolling occurs within separate sidebar, canvas and Agent regions.
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
          // The library hardcodes overflow: auto on inner panels, requiring style overrides; canvas views handle their own scrolling;
          // overlay canvases extend beneath Agent panels and must not be clipped by this layer.
          // eslint-disable-next-line shadcn/no-inline-styles -- Override the library inner inline overflow
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
        {/* Collapsed handles sit at column right edges and their hit areas extend outside shells, so hide them; keep disabled handles in DOM so the library creates no drag region at this boundary. */}
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
          // Collapsed width is 0; contents stay mounted at minimum width, invisible in place and clipped by this layer.
          // eslint-disable-next-line shadcn/no-inline-styles -- Override the library inner inline overflow
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
