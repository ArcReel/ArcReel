import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
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
import { EPISODE_VIEW_EDIT } from "@/app-routes";
import { MsgProtoBar } from "@/prototype/agent-messages/MsgProtoBar";
import { useRequestedAgentWidth } from "@/prototype/agent-messages/store";
import { publishMetrics, useShellProto } from "@/prototype/shell/store";
import { AgentRail, DragHandle, useElementWidth, useViewportWidth } from "@/prototype/shell/ShellParts";
import {
  AGENT_RAIL_WIDTH,
  CANVAS_MIN_WIDTH,
  COMPACT_BREAKPOINT,
  SIDEBAR_RAIL_WIDTH,
  WIDTH_LIMITS,
} from "@/prototype/shell/axes";

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
  void displayedPanelWidth;
  void handleResizeMouseDown;
  void handleResizeDoubleClick;

  if (!demoMode) {
    return (
      <PrototypeShell sseProjectName={sseProjectName}>{children}</PrototypeShell>
    );
  }

  return (
    <div
      className="flex h-screen flex-col"
      style={{ color: "var(--color-text)" }}
    >
      <TaskFailureListener projectName={sseProjectName} />
      <ScriptGenerationNoticeListener />
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      <DemoReadOnlyBanner />
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar />
        <main className="flex-1 overflow-hidden">{children}</main>
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
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PROTOTYPE（#2970）：按 ?variant= 与逐轴覆盖渲染的工作区外壳。不合并。
// ---------------------------------------------------------------------------

const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";

function PrototypeShell({
  sseProjectName,
  children,
}: {
  sseProjectName: string | null;
  children: React.ReactNode;
}) {
  const [location, setLocation] = useLocation();
  const search = useSearch();
  const { t } = useTranslation("dashboard");
  const { axes } = useShellProto();
  const viewport = useViewportWidth();
  const wide = viewport >= COMPACT_BREAKPOINT;
  const open = useAppStore((s) => s.assistantPanelOpen);
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);

  const inEpisode = /^\/episodes\/\d+/.test(location);
  const inEdit = inEpisode && new URLSearchParams(search).get("view") === EPISODE_VIEW_EDIT;

  const limits = WIDTH_LIMITS[axes.resize];
  const [sidebarWidth, setSidebarWidth] = useState(256);
  const [agentWidth, setAgentWidth] = useState<number>(limits.agentDefault);
  const [dragging, setDragging] = useState(false);
  // #2980：切换栏的宽度按钮直接设定 Agent 面板宽度
  const requestedAgentWidth = useRequestedAgentWidth();
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型
    if (requestedAgentWidth) setAgentWidth(requestedAgentWidth.width);
  }, [requestedAgentWidth]);
  useEffect(() => {
    // 切换调宽轴时回到该轴的默认宽度
    /* eslint-disable react-hooks/set-state-in-effect -- 原型 */
    setSidebarWidth(256);
    setAgentWidth(WIDTH_LIMITS[axes.resize].agentDefault);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [axes.resize]);

  // 侧栏折叠：有「强制折叠」理由时默认折叠，用户可临时展开，理由变化或换页后复位
  const forcedReason =
    !wide && axes.compact !== "keep"
      ? "compact"
      : axes.autoCollapse === "episode" && inEpisode
        ? "episode"
        : axes.autoCollapse === "edit" && inEdit
          ? "edit"
          : null;
  const [userCollapsed, setUserCollapsed] = useState(false);
  const [tempExpanded, setTempExpanded] = useState(false);
  const resetKey = `${forcedReason}|${location}|${inEdit}`;
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型
    setTempExpanded(false);
  }, [resetKey]);
  const sidebarCollapsed = forcedReason ? !tempExpanded : userCollapsed;
  const toggleSidebar = () =>
    forcedReason ? setTempExpanded((v) => !v) : setUserCollapsed((v) => !v);

  const overlay =
    axes.panel === "overlay" || (!wide && axes.compact === "railSidebarOverlay");

  const [rowRef, rowWidth] = useElementWidth<HTMLDivElement>();
  const sidebarPx = sidebarCollapsed
    ? SIDEBAR_RAIL_WIDTH
    : Math.max(limits.sidebar[0], Math.min(limits.sidebar[1], sidebarWidth));
  const railPx = axes.reopen === "rail" && !open ? AGENT_RAIL_WIDTH : 0;
  // 挤压模式给画布留 480px 下限；覆盖模式至少露出 64px 画布
  const agentCap = overlay
    ? rowWidth - sidebarPx - 64
    : rowWidth - sidebarPx - CANVAS_MIN_WIDTH;
  const agentPx = Math.max(
    limits.agent[0],
    Math.min(agentWidth, limits.agent[1], agentCap),
  );
  const canvasPx = Math.max(
    0,
    rowWidth - sidebarPx - railPx - (open && !overlay ? agentPx : 0),
  );

  useEffect(() => {
    publishMetrics({
      viewport,
      tier: wide ? "standard" : "compact",
      sidebar: sidebarPx,
      canvas: canvasPx,
      agent: open ? agentPx : 0,
      agentMode: open ? (overlay ? "覆盖" : "挤压") : "收起",
    });
  }, [viewport, wide, sidebarPx, canvasPx, agentPx, open, overlay]);

  // 覆盖模式下 Esc 收起（焦点在面板内时）
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!overlay || !open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && panelRef.current?.contains(document.activeElement)) {
        toggleAssistantPanel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay, open, toggleAssistantPanel]);

  const motion = axes.motion;
  const widthTransition =
    dragging || overlay
      ? undefined
      : motion === "width300"
        ? "width 300ms ease-in-out"
        : motion === "slide"
          ? `width 200ms ${EASE_OUT}`
          : undefined;
  const overlayTransition =
    !overlay || dragging
      ? undefined
      : motion === "slide"
        ? `transform 200ms ${EASE_OUT}, visibility 0s linear ${open ? "0s" : "200ms"}`
        : motion === "width300"
          ? `opacity 200ms ease, visibility 0s linear ${open ? "0s" : "200ms"}`
          : undefined;
  const contentFade =
    motion === "fade" ? (open ? "opacity 150ms ease-out" : "none") : undefined;

  const agentHandle =
    axes.resize !== "none" && open ? (
      <DragHandle
        edge="left"
        label="调整 Agent 面板宽度"
        value={agentPx}
        min={limits.agent[0]}
        max={Math.max(limits.agent[0], Math.min(limits.agent[1], agentCap))}
        onChange={setAgentWidth}
        onReset={() => setAgentWidth(limits.agentDefault)}
        onDragging={setDragging}
      />
    ) : null;

  const panelInner = (
    <div
      aria-hidden={!open}
      inert={!open}
      className="h-full"
      style={{
        opacity: open ? 1 : 0,
        transition: contentFade,
        visibility: open ? "visible" : "hidden",
      }}
    >
      <AgentCopilot />
    </div>
  );

  return (
    <div className="flex h-dvh flex-col text-foreground">
      <TaskFailureListener projectName={sseProjectName} />
      <ScriptGenerationNoticeListener />
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      <div ref={rowRef} className="relative flex min-h-0 flex-1 overflow-hidden">
        <div className="relative flex shrink-0">
          <AssetSidebar
            collapsed={sidebarCollapsed}
            onToggleCollapsed={toggleSidebar}
            width={sidebarPx}
            animate={!dragging}
          />
          {axes.resize === "both" && !sidebarCollapsed ? (
            <DragHandle
              edge="right"
              label="调整侧栏宽度"
              value={sidebarPx}
              min={limits.sidebar[0]}
              max={limits.sidebar[1]}
              onChange={setSidebarWidth}
              onReset={() => setSidebarWidth(256)}
              onDragging={setDragging}
            />
          ) : null}
        </div>
        <main className="min-w-0 flex-1 overflow-hidden">{children}</main>

        {overlay ? (
          <div
            ref={panelRef}
            className="absolute inset-y-0 right-0 z-30 border-l border-border bg-popover shadow-[-16px_0_40px_-12px_oklch(0_0_0/0.6)]"
            style={{
              width: agentPx,
              transform: motion === "slide" && !open ? "translateX(100%)" : "none",
              opacity: motion === "width300" && !open ? 0 : 1,
              visibility: open ? "visible" : "hidden",
              transition: overlayTransition,
            }}
          >
            {agentHandle}
            {panelInner}
          </div>
        ) : (
          <div
            ref={panelRef}
            className="relative shrink-0 overflow-hidden"
            style={{
              width: open ? agentPx : 0,
              borderLeft: open ? "1px solid var(--color-hairline)" : "none",
              transition: widthTransition,
            }}
          >
            {agentHandle}
            <div className="h-full" style={{ width: agentPx }}>
              {panelInner}
            </div>
          </div>
        )}

        {railPx > 0 ? <AgentRail /> : null}
      </div>

      {axes.reopen === "ball" && !open ? (
        <button
          type="button"
          onClick={toggleAssistantPanel}
          className={`fixed right-4 top-14 grid h-10 w-10 place-items-center rounded-xl ${UI_LAYERS.workspaceFloating}`}
          style={{
            background: "var(--color-primary)",
            color: "oklch(0.12 0 0)",
            boxShadow: "0 0 0 1px oklch(1 0 0 / 0.1), 0 6px 20px -6px oklch(0 0 0 / 0.6)",
          }}
          title={t("open_assistant_panel")}
          aria-label={t("open_assistant_panel")}
        >
          <Bot className="h-5 w-5" />
        </button>
      ) : null}
      <MsgProtoBar />
    </div>
  );
}
