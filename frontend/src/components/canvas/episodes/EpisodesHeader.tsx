import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, FilePlus, Loader2, Sparkles, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import type { EpisodesView } from "@/types";

import { EpisodePlanningPanel } from "./EpisodePlanningPanel";
import { remainingUnits } from "./episode-planning-model";
import { formatVolume, type SourceUploadMode } from "./episodes-view-model";

interface EpisodesHeaderProps {
  projectName: string;
  view: EpisodesView;
  episodeCount: number;
  /** 本项目有分集规划在排队或执行。 */
  planning: boolean;
  onUpload: (mode: SourceUploadMode) => void;
  onCreate: () => void;
}

/**
 * 「分集」视图的页头工具行：左侧是标题、集数与整本源文的分集进度，右侧是「AI 规划分集」弹层、新建一集与上传原文。
 * 有新的分集方案时规划按钮不可用，先在方案栏里采纳或放弃。
 */
export function EpisodesHeader({ projectName, view, episodeCount, planning, onUpload, onCreate }: EpisodesHeaderProps) {
  const { t } = useTranslation("dashboard");
  const [planOpen, setPlanOpen] = useState(false);
  const hasSource = view.files.length > 0;
  const started = view.cut_units > 0;
  const remaining = remainingUnits(view);
  const percent = view.units === 0 ? 0 : Math.round((view.cut_units / view.units) * 100);
  const replanPending = view.replan !== null;

  let planLabel: string;
  if (planning) planLabel = t("episodes_header_plan_running", { percent });
  else if (replanPending) planLabel = t("episodes_header_plan_pending");
  // 规划到了结尾，但切出集之间还夹着未切分的原文（删集留下的空当），要在集目录里逐段规划
  else if (started && remaining === 0)
    planLabel = t(view.cut_units < view.units ? "episodes_header_plan_gaps" : "episodes_header_plan_done");
  else if (started) planLabel = t("episode_planning_continue");
  else planLabel = t("episode_planning_start");

  return (
    <header className="@container/episodes-header flex h-12 shrink-0 items-center gap-3 border-b px-4">
      <h2 className="shrink-0 text-base font-semibold text-foreground">{t("workspace_nav_episodes")}</h2>
      <span className="num shrink-0 text-xs text-muted-foreground">
        {t("episodes_view_episode_count", { count: episodeCount })}
      </span>
      {hasSource ? (
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <Progress value={percent} aria-label={t("episodes_view_progress_label")} className="w-20 shrink-0" />
          <span className="num hidden truncate @min-[52rem]/episodes-header:inline">
            {t("episodes_view_progress", {
              cut: view.cut_units.toLocaleString(),
              total: formatVolume(t, view.units, view.unit),
            })}
          </span>
        </div>
      ) : null}
      <span className="flex-1" />
      {hasSource ? (
        <Popover open={planOpen} onOpenChange={setPlanOpen}>
          <PopoverTrigger
            render={
              <Button
                variant={remaining > 0 && !planning && !replanPending ? "secondary" : "ghost"}
                size="sm"
                disabled={replanPending}
              />
            }
          >
            {planning ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Sparkles aria-hidden data-icon="inline-start" />
            )}
            {planLabel}
            <ChevronDown aria-hidden data-icon="inline-end" />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-80">
            <EpisodePlanningPanel
              projectName={projectName}
              view={view}
              active={planning}
              onStarted={() => setPlanOpen(false)}
            />
          </PopoverContent>
        </Popover>
      ) : null}
      <Button variant="ghost" size="sm" onClick={onCreate} aria-label={t("episode_create_title")}>
        <FilePlus aria-hidden data-icon="inline-start" />
        <span className="hidden @min-[44rem]/episodes-header:inline">{t("episode_create_title")}</span>
      </Button>
      <ButtonGroup>
        <Button size="sm" onClick={() => onUpload("whole_source")}>
          <Upload aria-hidden data-icon="inline-start" />
          {t("source_upload_title")}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="icon-sm" aria-label={t("episodes_header_upload_modes")} />}>
            <ChevronDown aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-56">
            <DropdownMenuItem onClick={() => onUpload("whole_source")}>
              <span className="flex flex-col gap-0.5">
                <span>{t("source_upload_mode_whole")}</span>
                <span className="text-xs text-muted-foreground">{t("source_upload_mode_whole_hint")}</span>
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onUpload("episode")}>
              <span className="flex flex-col gap-0.5">
                <span>{t("source_upload_mode_episode")}</span>
                <span className="text-xs text-muted-foreground">{t("source_upload_mode_episode_hint")}</span>
              </span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </ButtonGroup>
    </header>
  );
}
