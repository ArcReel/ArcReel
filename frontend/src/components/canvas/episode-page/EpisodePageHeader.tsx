import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { episodeViewTabId, type EpisodeView, type EpisodeViewTab } from "./episode-view";

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

export function EpisodePageHeader({
  head,
  sourceEntry,
  progress,
  tabs,
  view,
  onViewChange,
  boardLabel,
  onActionsSlot,
}: {
  head: ReactNode;
  sourceEntry: ReactNode;
  progress: ReactNode;
  tabs: EpisodeViewTab[];
  view: EpisodeView;
  onViewChange: (view: EpisodeView) => void;
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
        {sourceEntry}
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
        <div className="relative flex min-w-0 flex-1 overflow-x-auto scroll-fade-x">
          <div ref={onActionsSlot} className="ml-auto flex shrink-0 items-center gap-1.5" />
        </div>
      </div>
    </header>
  );
}
