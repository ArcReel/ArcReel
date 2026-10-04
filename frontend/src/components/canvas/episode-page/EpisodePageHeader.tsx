import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { EpisodeView, EpisodeViewTab } from "./episode-view";

/** 视图 tab 的元素 id，视图区以它作为无障碍名。 */
export function episodeViewTabId(view: EpisodeView): string {
  return `episode-view-tab-${view}`;
}

/**
 * 集页页头，两行共约 85px：第一行是集头与行尾的制作进度入口，第二行是视图 tab 与当前视图的动作插槽。
 * 动作由各视图经 `EpisodeHeaderActions` 放进插槽，页头不认识具体动作。
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
  /** 制作进度入口；演示项目没有。 */
  progress: ReactNode;
  tabs: EpisodeViewTab[];
  view: EpisodeView;
  onViewChange: (view: EpisodeView) => void;
  /** 「分镜」tab 的名称：参考生视频项目称「视频单元」。 */
  boardLabel: string;
  onActionsSlot: (node: HTMLDivElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  const label: Record<EpisodeView, string> = {
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
            {tabs.map((tab) => (
              <TabsTrigger key={tab.view} value={tab.view} id={episodeViewTabId(tab.view)} disabled={tab.disabled}>
                {label[tab.view]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        {/* 动作多到放不下时横向滚动；插槽靠右，溢出时从左端开始滚，不裁掉第一个动作。 */}
        <div className="relative flex min-w-0 flex-1 overflow-x-auto scroll-fade-x">
          <div ref={onActionsSlot} className="ml-auto flex shrink-0 items-center gap-1.5" />
        </div>
      </div>
    </header>
  );
}
