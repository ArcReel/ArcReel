import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { PageSidebarDrawerButton } from "./PageShell";

interface PageHeaderProps {
  /** 「返回」按钮；返回到进入页面之前的位置时，用 `useReturnTo` 取得 `onClick`。 */
  back?: { label: string; onClick: () => void };
  title?: string;
  subtitle?: string;
  /** 标题组之后的自定义内容，如项目大厅的品牌与搜索。 */
  children?: ReactNode;
  /** 靠右的页面级动作。 */
  actions?: ReactNode;
}

/**
 * 页面外壳的顶栏，高 56px：「返回 | 标题 | 副标题」成组靠左，返回箭头与侧栏图标在同一条竖线上，
 * 页面级动作靠右。
 *
 * 移动档：外壳有侧栏时最左侧出现菜单按钮打开抽屉；动作区横向可滚动，不折行挤占标题。
 */
export function PageHeader({ back, title, subtitle, children, actions }: PageHeaderProps) {
  return (
    <header
      data-slot="mobile-safe-top"
      className="@container/header flex min-h-14 shrink-0 items-center gap-2 border-b border-border pr-3 pl-2 @md/header:gap-3 @md/header:pr-6 @md/header:pl-3 xl:pr-8"
    >
      {/* 左侧不压到 0 宽：品牌图标与返回按钮是 shrink-0，容器再小就会溢出到动作区底下。
          用 min-w-fit 保证至少容下这些不收缩的内容，余下的宽度交给动作区滚动。 */}
      <div className="flex min-w-fit flex-1 items-center gap-2 @md/page:gap-3">
        <PageSidebarDrawerButton />
        {back && (
          <>
            <Button variant="ghost" onClick={back.onClick}>
              <ChevronLeft aria-hidden data-icon="inline-start" />
              {back.label}
            </Button>
            <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
          </>
        )}
        {title && (
          <h1 className="min-w-0 text-base font-medium">
            <TruncatedText text={title} />
          </h1>
        )}
        {subtitle && <TruncatedText text={subtitle} className="hidden text-sm text-muted-foreground @md/page:block" />}
        {children}
      </div>
      {/* 动作区让位给标题与品牌：宽度不足时它自己横向滚动，而不是把左侧挤到 0 宽。 */}
      {actions && (
        <div className="relative flex min-w-0 items-center gap-2 overflow-x-auto scroll-fade-x">
          {actions}
        </div>
      )}
    </header>
  );
}
