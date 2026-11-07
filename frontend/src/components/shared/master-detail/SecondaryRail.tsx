import { useState, type ReactNode } from "react";
import { Link } from "wouter";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface SecondaryRailItem {
  /** Unique across entire secondary rails, compared with `activeId`. */
  id: string;
  label: string;
  /** Second lines: states/counts, e.g. 2 configured keys or 3 models. */
  description?: string;
  /** Left of names in standard mode, sole visible content in compact mode. Callers set sizes (`size-4`). */
  icon: ReactNode;
  /** Entry-selection URLs; selections live in URLs and switching uses routes, allowing leave guards to cover switches. */
  href: string;
}

export interface SecondaryRailGroup {
  id: string;
  /** Tab names for multiple groups; accessible group-list names in compact mode. */
  label: string;
  items: SecondaryRailItem[];
  /** Actions at group ends, e.g. Add custom provider; excluded from tab counts. */
  action?: SecondaryRailItem;
  /** Empty-group explanations, standard mode only. */
  emptyText?: string;
}

interface SecondaryRailProps {
  /** Navigation accessible names, e.g. Provider lists. */
  label: string;
  groups: SecondaryRailGroup[];
  activeId: string | null;
  /** Replace current history on switches so browser Back never traverses entries. */
  replace?: boolean;
}

/**
 * Independently scrolling secondary rails for full-bleed master/detail layouts, shared by providers/API endpoints/Agent memory.
 *
 * Two forms follow `@container/page` widths rather than viewports:
 * - Standard (content ≥64rem): 264px, two-line entries (names; states/counts). Multiple groups use top tabs showing one group at a time,
 *   with counts; tabs follow selected-entry groups until manually switched, then remember choices.
 * - Compact: 56px icon rails, revealing names/second lines on hover/focus; multiple groups stack vertically.
 *
 * Both forms exist in DOM, container queries showing only one; hidden forms use `display: none` and leave accessibility trees.
 * jsdom computes no styles, so Vitest sees both: scope entry queries within `getByRole("tabpanel")`, or use `getAllBy*`.
 */
export function SecondaryRail({ label, groups, activeId, replace }: SecondaryRailProps) {
  const [chosenTab, setChosenTab] = useState<string | null>(null);
  const selectedGroup = groups.find(
    (group) => group.items.some((item) => item.id === activeId) || group.action?.id === activeId,
  );
  const tab = chosenTab ?? selectedGroup?.id ?? groups[0]?.id;

  return (
    <nav
      aria-label={label}
      className="relative w-14 shrink-0 overflow-y-auto border-r border-border @5xl/page:w-66"
    >
      <div className="hidden px-3 py-4 @5xl/page:block">
        {groups.length > 1 ? (
          <Tabs value={tab} onValueChange={(value: string) => setChosenTab(value)}>
            <TabsList className="w-full">
              {groups.map((group) => (
                <TabsTrigger key={group.id} value={group.id}>
                  {group.label}
                  <span className="tabular-nums">{group.items.length}</span>
                </TabsTrigger>
              ))}
            </TabsList>
            {groups.map((group) => (
              <TabsContent key={group.id} value={group.id}>
                <SecondaryRailList group={group} activeId={activeId} replace={replace} />
              </TabsContent>
            ))}
          </Tabs>
        ) : (
          groups[0] && <SecondaryRailList group={groups[0]} activeId={activeId} replace={replace} />
        )}
      </div>

      <div className="flex flex-col gap-3 px-2 py-3 @5xl/page:hidden">
        {groups.map((group, index) => (
          <ul
            key={group.id}
            aria-label={group.label}
            className={cn("flex flex-col items-center gap-1", index > 0 && "border-t border-border pt-3")}
          >
            {[...group.items, ...(group.action ? [group.action] : [])].map((item) => (
              <li key={item.id}>
                <CompactItem item={item} active={item.id === activeId} replace={replace} />
              </li>
            ))}
          </ul>
        ))}
      </div>
    </nav>
  );
}

/**
 * Standard entry lists: two-line entries (names; states/counts) followed by group-end actions, without independent scrolling.
 * Use standalone lists when width-limited pages cannot fit rails (e.g. project-settings Project memory), scrolling page bodies instead.
 */
export function SecondaryRailList({
  group,
  activeId,
  replace,
}: {
  group: SecondaryRailGroup;
  activeId: string | null;
  replace?: boolean;
}) {
  return (
    <ul aria-label={group.label} className="flex flex-col gap-0.5">
      {group.items.length === 0 && group.emptyText && (
        <li className="px-2.5 py-1 text-xs text-muted-foreground">{group.emptyText}</li>
      )}
      {[...group.items, ...(group.action ? [group.action] : [])].map((item) => {
        const active = item.id === activeId;
        return (
          <li key={item.id}>
            <Link
              href={item.href}
              replace={replace}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex min-h-8 items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                active
                  ? "bg-primary/15 text-foreground"
                  : "text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
              )}
            >
              <span aria-hidden className="flex shrink-0 text-muted-foreground">
                {item.icon}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <TruncatedText text={item.label} focusable={false} />
                {item.description && (
                  <span className="truncate text-xs text-muted-foreground">{item.description}</span>
                )}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function CompactItem({ item, active, replace }: { item: SecondaryRailItem; active: boolean; replace?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href={item.href}
            replace={replace}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex size-10 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              active ? "bg-primary/15 text-foreground" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          />
        }
      >
        <span aria-hidden className="flex">
          {item.icon}
        </span>
        {/* Icon rails show only icons; names/second lines remain for screen readers. */}
        <span className="sr-only">{item.label}</span>
        {item.description && <span className="sr-only"> {item.description}</span>}
      </TooltipTrigger>
      <TooltipContent side="right">
        <span className="flex flex-col">
          <span>{item.label}</span>
          {item.description && <span className="text-background/70">{item.description}</span>}
        </span>
      </TooltipContent>
    </Tooltip>
  );
}
