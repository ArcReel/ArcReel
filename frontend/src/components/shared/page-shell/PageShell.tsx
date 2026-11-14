import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLocation, useSearch } from "wouter";
import { Menu } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useMobileTier } from "@/hooks/useMobileTier";

/**
 * 内容区的容器档位。
 * - `constrained`（限宽）：内容列最大 760px、靠左，外壳主体滚动并提供内边距。用于表单与设置。
 * - `full`（铺满）：不设上限，外壳主体滚动并提供内边距。用于列表、网格与表格。
 * - `bleed`（全出血）：外壳主体不滚动、不加内边距，区段占满高度自己分栏，每栏各自滚动。用于主从布局与编辑器。
 */
export type ContainerTier = "constrained" | "full" | "bleed";

interface FooterSlot {
  tier: Exclude<ContainerTier, "bleed">;
  element: HTMLElement | null;
}

const FooterSlotContext = createContext<FooterSlot | null>(null);

/** 移动档抽屉的开关，供 `PageHeader` 渲染菜单按钮。 */
const SidebarDrawerContext = createContext<(() => void) | null>(null);

/**
 * 侧栏当前是否渲染在移动抽屉里。`PageSidebar` 读它切换宽度与边框，
 * 页面不必知道自己处在哪一档。
 */
export const SidebarDrawerModeContext = createContext(false);

interface PageShellProps {
  /** 顶栏，传 `PageHeader`。 */
  header: ReactNode;
  /** 侧栏，传 `PageSidebar`；项目大厅、资产库这类单视图页面不传。 */
  sidebar?: ReactNode;
  /** 侧栏导航的可访问名称，移动抽屉作为对话框时用作标题。 */
  sidebarLabel?: string;
  tier: ContainerTier;
  /**
   * 移动档把内容字号整体收小一档（正文 13px、辅助 12px、标题 16px），让一屏多放一两条信息。
   * 用于设置这类表单密集的页面；列表与画布不需要，保持默认字号。
   */
  compactText?: boolean;
  children: ReactNode;
}

/**
 * 全局设置、项目设置、项目大厅与资产库共用的页面外壳：顶栏横跨全宽，侧栏贴左，内容列紧贴侧栏靠左，
 * 宽窗口多出的空白留在右侧。外壳根节点占满视口高度，文档本身不滚动；滚动只发生在侧栏、外壳主体
 * （限宽与铺满档）或全出血区段的各栏里。
 *
 * 外壳是唯一按视口切换标准档与紧凑档的地方（`xl`，1280）：侧栏 224 / 200px，内容内边距 32 / 24px。
 * 内容列是名为 `page` 的尺寸容器，区段内部按 `@md/page:` 这类容器查询响应宽度。
 *
 * 移动档（<768px）：侧栏不占文档流，收进左侧 Sheet，由顶栏的菜单按钮打开；内容内边距降到 16px。
 * 抽屉的开合、Esc 与遮罩点击都由 Sheet 原语处理。
 *
 * 限宽与铺满档在主体下方有一行固定的保存栏，区段用 `PageShellFooter` 把保存栏渲染进来；
 * 没有区段渲染保存栏时这一行不显示。全出血档没有这一行，保存栏放在区段自己的详情栏底部。
 */
export function PageShell({ header, sidebar, sidebarLabel, tier, compactText, children }: PageShellProps) {
  const [footerElement, setFooterElement] = useState<HTMLDivElement | null>(null);
  const footerSlot = useMemo<FooterSlot | null>(
    () => (tier === "bleed" ? null : { tier, element: footerElement }),
    [tier, footerElement],
  );
  const mobile = useMobileTier();
  const [location] = useLocation();
  // 设置页的分区记在查询串上，切分区时 path 不变，必须一起参与抽屉的复位判断。
  const search = useSearch();

  // 抽屉只在移动档、且外壳带侧栏时存在。离开这个组合（换页、切到桌面档）时按渲染期
  // 状态校正收起，不用 effect 写 state。地址变化也算离开：点抽屉里的导航项后抽屉要收起，
  // 否则它会盖住刚打开的页面。
  const drawerKey = mobile && sidebar ? `mobile:${location}?${search}` : "hidden";
  const [drawer, setDrawer] = useState({ key: drawerKey, open: false });
  if (drawer.key !== drawerKey) setDrawer({ key: drawerKey, open: false });
  const openDrawer = useCallback(() => setDrawer({ key: drawerKey, open: true }), [drawerKey]);
  const setDrawerOpen = useCallback((open: boolean) => setDrawer({ key: drawerKey, open }), [drawerKey]);

  const inlineSidebar = sidebar && !mobile ? sidebar : null;
  const drawerSidebar = sidebar && mobile ? sidebar : null;

  return (
    // 外壳根节点与各滚动容器都是定位元素：sr-only 等绝对定位的子元素以它们为包含块，
    // 不会逃出滚动容器把文档撑高。
    <div
      className="relative flex h-dvh flex-col overflow-hidden text-foreground"
      data-mobile-compact-text={compactText ? "" : undefined}
    >
      <SidebarDrawerContext.Provider value={drawerSidebar ? openDrawer : null}>
        {header}
      </SidebarDrawerContext.Provider>
      <div className="flex min-h-0 flex-1">
        {inlineSidebar}
        <div className="flex min-w-0 flex-1 flex-col">
          <FooterSlotContext.Provider value={footerSlot}>
            {tier === "bleed" ? (
              <main className="@container/page relative flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
            ) : (
              <main className="relative min-h-0 min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
                <div className="p-4 @md/page:p-6 xl:p-8">
                  <div className={cn("@container/page min-w-0", tier === "constrained" && "max-w-190")}>
                    {children}
                  </div>
                </div>
              </main>
            )}
          </FooterSlotContext.Provider>
          {tier !== "bleed" && (
            // 区段经 PageShellFooter 渲染进来；没有内容时整行隐藏。
            <div
              ref={setFooterElement}
              data-slot="mobile-safe-bottom"
              className="shrink-0 border-t border-border bg-card px-4 py-3 empty:hidden md:px-6 xl:px-8"
            />
          )}
        </div>
      </div>
      {drawerSidebar && (
        <Sheet open={drawer.open} onOpenChange={setDrawerOpen}>
          <SheetContent id="page-mobile-sidebar" side="left" className="max-md:w-full max-md:max-w-full">
            <SheetHeader>
              <SheetTitle>{sidebarLabel}</SheetTitle>
            </SheetHeader>
            <SheetBody>
              <SidebarDrawerModeContext.Provider value>{drawerSidebar}</SidebarDrawerModeContext.Provider>
            </SheetBody>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}

/**
 * 移动档下 `PageHeader` 的菜单按钮：有侧栏时打开抽屉，没有侧栏时不渲染。
 * 供 `PageHeader` 内部使用，不对外导出。
 */
export function PageSidebarDrawerButton() {
  const { t } = useTranslation("common");
  const open = useContext(SidebarDrawerContext);
  if (!open) return null;
  return (
    <Button variant="ghost" size="icon" aria-label={t("open_navigation")} aria-controls="page-mobile-sidebar" onClick={open}>
      <Menu aria-hidden />
    </Button>
  );
}

/**
 * 把保存栏渲染进外壳的固定底行：不随主体滚动，内层与内容列同宽、同起点。
 * 铺满档里的表单若自己限宽到 760px，传 `constrained`，保存栏与表单列同宽。
 * 不在外壳里（如单测直接渲染区段）或处于全出血档时，原地渲染。
 */
export function PageShellFooter({ children, constrained = false }: { children: ReactNode; constrained?: boolean }) {
  const slot = useContext(FooterSlotContext);
  if (!slot) return children;
  if (!slot.element) return null;
  return createPortal(
    <div className={cn("min-w-0", (constrained || slot.tier === "constrained") && "max-w-190")}>{children}</div>,
    slot.element,
  );
}
