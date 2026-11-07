import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { episodeViewTabId, type EpisodeView, type EpisodeViewTab } from "./episode-view";

/**
 * Disabled view tabs currently occur only without formal scripts (storyboards/grids); hover and screen readers explain availability after script generation.
 * Disabled tabs ignore pointer events, so wrap hints outside; arrow keys can still focus them, showing hints on focus too.
 */
function DisabledViewTab({ view, label }: { view: EpisodeView; label: string }) {
  const { t } = useTranslation("dashboard");
  const hintId = useId();
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="flex h-full" />}>
        <TabsTrigger value={view} id={episodeViewTabId(view)} disabled aria-describedby={hintId}>
          {label}
        </TabsTrigger>
        <span id={hintId} className="sr-only">
          {t("episode_view_needs_script")}
        </span>
      </TooltipTrigger>
      <TooltipContent>{t("episode_view_needs_script")}</TooltipContent>
    </Tooltip>
  );
}

/**
 * Two-row episode header, about 85px: episode head/progress entry first, view tabs/current-view action slot second.
 * Views populate the slot through `EpisodeHeaderActions`; headers do not know specific actions.
 */
export function EpisodePageHeader({
  head,
  progress,
  tabs,
  view,
  onViewChange,
  boardLabel,
  onActionsSlot,
}: {
  head: ReactNode;
  /** Production-progress entry, absent in demo projects. */
  progress: ReactNode;
  tabs: EpisodeViewTab[];
  view: EpisodeView;
  onViewChange: (view: EpisodeView) => void;
  /** Storyboard-tab label: video units for reference-to-video projects. */
  boardLabel: string;
  onActionsSlot: (node: HTMLDivElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  const label: Record<EpisodeView, string> = {
    setting: t("episode_view_setting"),
    plan: t("episode_view_plan"),
    grid: t("episode_view_grid"),
    board: boardLabel,
    edit: t("episode_view_edit"),
  };
  return (
    <header className="shrink-0 border-b border-border">
      <div className="flex h-11 items-center gap-3 px-4">
        {head}
        {progress}
      </div>
      <div className="flex h-10 items-center gap-3 px-4">
        <Tabs value={view} onValueChange={(next: EpisodeView) => onViewChange(next)} className="shrink-0">
          <TabsList aria-label={t("episode_view_aria")}>
            {tabs.map((tab) =>
              tab.disabled ? (
                <DisabledViewTab key={tab.view} view={tab.view} label={label[tab.view]} />
              ) : (
                <TabsTrigger key={tab.view} value={tab.view} id={episodeViewTabId(tab.view)}>
                  {label[tab.view]}
                </TabsTrigger>
              ),
            )}
          </TabsList>
        </Tabs>
        {/* Horizontally scroll overflowing actions; right-aligned slot starts scrolling at the left edge so the first action remains accessible. */}
        <div className="relative flex min-w-0 flex-1 overflow-x-auto scroll-fade-x">
          <div ref={onActionsSlot} className="ml-auto flex shrink-0 items-center gap-1.5" />
        </div>
      </div>
    </header>
  );
}
