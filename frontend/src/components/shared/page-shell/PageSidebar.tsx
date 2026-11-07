import { useId, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Link } from "wouter";
import { cn } from "cn";

export interface PageSidebarItem {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Selection URLs; selections live in URLs and routing switches lets leave guards cover them. */
  href: string;
  /** Marks right of labels, e.g. configuration-warning dots/N overrides; supply accessible names when announcements are needed. */
  badge?: ReactNode;
  /** Onboarding anchors (values from `ONBOARDING_ANCHORS`). */
  onboardingAnchor?: string;
}

export interface PageSidebarGroup {
  id: string;
  /** Group headings; standalone entries omit them. */
  label?: string;
  items: PageSidebarItem[];
}

interface PageSidebarProps {
  /** Navigation accessible names. */
  label: string;
  groups: PageSidebarGroup[];
  activeId: string;
  /** Replace current history when switching so browser Back returns directly to locations before entering these pages. */
  replace?: boolean;
}

/** Page-shell sidebars: left-aligned, independently scrolling, 224px standard / 200px compact. */
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
