import { Link } from "wouter";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { Button } from "@/components/ui/button";

interface EndpointsRailProps {
  /** 导航区的可访问名称，如「调用端点列表」。 */
  label: string;
  groups: SecondaryRailGroup[];
  activeId: string | null;
  /** 折叠后只留折叠按钮，详情栏独占剩余宽度。 */
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

/**
 * 调用端点在移动档的二级栏：竖排端点名称列表，占内容区 1/4 宽，可折叠。
 *
 * 移动档原本的图标横排只能看见锁与 workflow 图标，分不清是哪个端点；这里按名称列出，
 * 名称在窄列里换行完整显示。分组标题在多组时出现（我的端点 / 内置）。
 */
export function EndpointsRail({ label, groups, activeId, collapsed, onCollapsedChange }: EndpointsRailProps) {
  const { t } = useTranslation("dashboard");
  return (
    <nav
      aria-label={label}
      className={cn(
        "relative flex shrink-0 flex-col border-r border-border",
        collapsed ? "w-11" : "w-1/4 min-w-20",
      )}
    >
      <div className={cn("flex shrink-0 py-1", collapsed ? "justify-center" : "justify-end pr-1")}>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={!collapsed}
          aria-label={collapsed ? t("sidebar_expand") : t("sidebar_collapse")}
          onClick={() => onCollapsedChange(!collapsed)}
        >
          {collapsed ? <PanelLeftOpen aria-hidden /> : <PanelLeftClose aria-hidden />}
        </Button>
      </div>

      {!collapsed && (
        <div className="relative min-h-0 flex-1 overflow-y-auto px-1 pb-2">
          {groups.map((group) => (
            <div key={group.id} className="flex flex-col gap-0.5">
              {groups.length > 1 && (
                <p className="px-1.5 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">{group.label}</p>
              )}
              {group.items.length === 0 && group.emptyText && (
                <p className="px-1.5 py-1 text-xs leading-tight text-muted-foreground">{group.emptyText}</p>
              )}
              {[...group.items, ...(group.action ? [group.action] : [])].map((item) => {
                const active = item.id === activeId;
                return (
                  <Link
                    key={item.id}
                    href={item.href}
                    replace
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex min-h-11 items-center rounded-md px-1.5 py-1.5 text-xs transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      active
                        ? "bg-primary/15 text-foreground"
                        : "text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
                    )}
                  >
                    <span className="min-w-0 flex-1 leading-tight break-words">{item.label}</span>
                    {item.description && <span className="sr-only"> {item.description}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </nav>
  );
}
