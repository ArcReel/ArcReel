import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "cn";

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

interface PageShellProps {
  /** 顶栏，传 `PageHeader`。 */
  header: ReactNode;
  /** 侧栏，传 `PageSidebar`；项目大厅、资产库这类单视图页面不传。 */
  sidebar?: ReactNode;
  tier: ContainerTier;
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
 * 限宽与铺满档在主体下方有一行固定的保存栏，区段用 `PageShellFooter` 把保存栏渲染进来；
 * 没有区段渲染保存栏时这一行不显示。全出血档没有这一行，保存栏放在区段自己的详情栏底部。
 */
export function PageShell({ header, sidebar, tier, children }: PageShellProps) {
  const [footerElement, setFooterElement] = useState<HTMLDivElement | null>(null);
  const footerSlot = useMemo<FooterSlot | null>(
    () => (tier === "bleed" ? null : { tier, element: footerElement }),
    [tier, footerElement],
  );

  return (
    // 外壳根节点与各滚动容器都是定位元素：sr-only 等绝对定位的子元素以它们为包含块，
    // 不会逃出滚动容器把文档撑高。
    <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
      {header}
      <div className="flex min-h-0 flex-1">
        {sidebar}
        <div className="flex min-w-0 flex-1 flex-col">
          <FooterSlotContext.Provider value={footerSlot}>
            {tier === "bleed" ? (
              <main className="@container/page relative flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
            ) : (
              <main className="relative min-h-0 min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
                <div className="p-6 xl:p-8">
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
              className="shrink-0 border-t border-border bg-card px-6 py-3 empty:hidden xl:px-8"
            />
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 把保存栏渲染进外壳的固定底行：不随主体滚动，内层与内容列同宽、同起点。
 * 不在外壳里（如单测直接渲染区段）或处于全出血档时，原地渲染。
 */
export function PageShellFooter({ children }: { children: ReactNode }) {
  const slot = useContext(FooterSlotContext);
  if (!slot) return children;
  if (!slot.element) return null;
  return createPortal(
    <div className={cn("min-w-0", slot.tier === "constrained" && "max-w-190")}>{children}</div>,
    slot.element,
  );
}
