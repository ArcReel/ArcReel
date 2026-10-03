import { useId, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Link } from "wouter";
import { cn } from "cn";

export interface PageSidebarItem {
  id: string;
  label: string;
  icon: LucideIcon;
  /** 选中这一项的地址。选中项记在地址里，切换经路由，离开拦截因此覆盖切换。 */
  href: string;
  /** 标签右侧的标记，如配置问题的警告点、「N 项覆盖」。需要读屏播报时自带可访问名称。 */
  badge?: ReactNode;
  /** 新手引导锚点（`ONBOARDING_ANCHORS` 中的值）。 */
  onboardingAnchor?: string;
}

export interface PageSidebarGroup {
  id: string;
  /** 分组标题；单独成项的条目不设标题。 */
  label?: string;
  items: PageSidebarItem[];
}

interface PageSidebarProps {
  /** 导航区的可访问名称。 */
  label: string;
  groups: PageSidebarGroup[];
  activeId: string;
  /** 切换时替换当前历史记录，浏览器「后退」直接回到进入页面之前的位置。 */
  replace?: boolean;
}

/** 页面外壳的侧栏：贴左、独立滚动，标准档 224px、紧凑档 200px。 */
export function PageSidebar({ label, groups, activeId, replace }: PageSidebarProps) {
  return (
    <nav
      aria-label={label}
      className="relative flex w-50 shrink-0 flex-col gap-5 overflow-y-auto border-r border-border px-3 py-4 xl:w-56"
    >
      {groups.map((group) => (
        <SidebarGroup key={group.id} group={group} activeId={activeId} replace={replace} />
      ))}
    </nav>
  );
}

function SidebarGroup({
  group,
  activeId,
  replace,
}: {
  group: PageSidebarGroup;
  activeId: string;
  replace?: boolean;
}) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-1">
      {group.label && (
        <p id={labelId} className="px-2 text-xs font-medium text-muted-foreground">
          {group.label}
        </p>
      )}
      <ul aria-labelledby={group.label ? labelId : undefined} className="flex flex-col gap-0.5">
        {group.items.map((item) => {
          const active = item.id === activeId;
          const Icon = item.icon;
          return (
            <li key={item.id}>
              <Link
                href={item.href}
                replace={replace}
                aria-current={active ? "page" : undefined}
                data-onboarding={item.onboardingAnchor}
                className={cn(
                  "flex min-h-8 items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  active
                    ? "bg-primary/15 text-foreground"
                    : "text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <Icon aria-hidden className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
                <span className="min-w-0 flex-1">{item.label}</span>
                {item.badge}
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
