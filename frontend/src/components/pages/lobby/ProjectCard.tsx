/**
 * 项目大厅的海报卡。引导的演示卡也用它，切到只读形态：「项目推进后长这样」这句话只有在演示卡与
 * 真实卡片是同一份实现时才不会随时间说谎。只读卡点进去是只读工作台，卡上不带「更多」菜单。
 */

import { Link } from "wouter";
import { Download, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { formatRelativeTime, isJustNow } from "@/utils/date-format";
import { getProjectDisplayName } from "@/utils/project-display";
import type { ProjectStatus, ProjectSummary } from "@/types";
import { asProjectStatus, projectProgress, repairReasonOf, type ProjectProgress } from "./lobby-projects";

export interface ProjectCardActions {
  onRename: (project: ProjectSummary) => void;
  onExport: (project: ProjectSummary) => void;
  onDelete: (project: ProjectSummary) => void;
}

/** 大厅里的真实卡片必须给 `actions`；只读演示卡不接受它。 */
type ProjectCardProps = { project: ProjectSummary } & (
  | { readOnly: true; actions?: never }
  | { readOnly?: false; actions: ProjectCardActions }
);

const PROGRESS_DOT: Record<ProjectProgress, string> = {
  empty: "bg-muted-foreground",
  in_progress: "bg-primary",
  completed: "bg-good",
};

export function ProjectCard({ project, readOnly, actions }: ProjectCardProps) {
  const { t, i18n } = useTranslation(["dashboard", "onboarding"]);
  const status = asProjectStatus(project.status);
  const progress = projectProgress(status);
  const title = getProjectDisplayName(project.title, t("dashboard:untitled_project"));
  const repairReason = repairReasonOf(status);

  return (
    <article
      className="group relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card transition-colors duration-fast hover:border-primary/40 has-focus-visible:border-ring"
    >
      <Link
        href={`/app/projects/${project.name}`}
        className="flex flex-1 flex-col text-foreground no-underline outline-none"
      >
        <Poster project={project} title={title} />
        <div className="flex min-w-0 flex-1 flex-col gap-2 px-3.5 pt-3 pb-3.5">
          <h3 className="min-w-0 text-base font-medium">
            <TruncatedText text={title} focusable={false} />
          </h3>
          <div className="flex items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5 text-subtle-foreground">
              <span aria-hidden className={cn("size-1.5 rounded-full", PROGRESS_DOT[progress])} />
              {t(`dashboard:lobby_card_status_${progress}`)}
            </span>
            {status?.needs_repair && (
              <span className="rounded-full bg-warn/15 px-1.5 text-warn">{t("dashboard:lobby_card_needs_repair")}</span>
            )}
          </div>
          {repairReason && (
            <p className="line-clamp-2 text-xs break-words text-muted-foreground">{repairReason}</p>
          )}
          <EpisodeStrip summary={status?.episodes_summary} />
          <p className="mt-auto text-xs text-muted-foreground">
            {[episodesText(status, progress, t), updatedText(project.last_activity_at, i18n.language, t)]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {readOnly && <span className="sr-only">{t("onboarding:demo_banner_title")}</span>}
        </div>
      </Link>

      {actions && (
        // 有悬停能力的设备上平时隐藏，悬停、聚焦或菜单打开时出现；触屏设备上常驻。
        <div className="absolute top-2 right-2 transition-opacity duration-fast pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-has-focus-visible:opacity-100 pointer-fine:has-aria-expanded:opacity-100">
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="secondary" size="icon-sm" aria-label={t("dashboard:lobby_card_actions", { title })} />
              }
            >
              <MoreHorizontal aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => actions.onRename(project)}>
                <Pencil aria-hidden />
                {t("dashboard:lobby_card_rename")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => actions.onExport(project)}>
                <Download aria-hidden />
                {t("dashboard:lobby_card_export")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => actions.onDelete(project)}>
                <Trash2 aria-hidden />
                {t("dashboard:lobby_card_delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
    </article>
  );
}

/** 海报 2:1。没有缩略图时用衬线字把标题写在海报上，不留一块无意义的色块。 */
function Poster({ project, title }: { project: ProjectSummary; title: string }) {
  return (
    <div className="relative aspect-2/1 overflow-hidden bg-linear-to-br from-primary/30 via-muted to-background">
      {project.thumbnail ? (
        <img
          src={project.thumbnail}
          alt=""
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <p aria-hidden className="absolute inset-x-4 bottom-3 line-clamp-2 font-editorial text-2xl break-words">
          {title}
        </p>
      )}
    </div>
  );
}

/** 每集一格：已完成、制作中、已有脚本、尚未开始。数量已写在下方的次要文字里，这里只作示意。 */
function EpisodeStrip({ summary }: { summary: ProjectStatus["episodes_summary"] | undefined }) {
  if (!summary || summary.total === 0) return null;
  const inProductionEnd = summary.completed + summary.in_production;
  // `scripted` 数的是有脚本的集，已包含完成与制作中的集。
  const scriptedEnd = Math.max(summary.scripted, inProductionEnd);
  return (
    <div aria-hidden className="flex gap-0.5">
      {Array.from({ length: summary.total }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-1 flex-1 rounded-full",
            i < summary.completed
              ? "bg-good"
              : i < inProductionEnd
                ? "bg-primary"
                : i < scriptedEnd
                  ? "bg-muted-foreground"
                  : "bg-muted",
          )}
        />
      ))}
    </div>
  );
}

type Translate = ReturnType<typeof useTranslation>["t"];

function episodesText(status: ProjectStatus | null, progress: ProjectProgress, t: Translate): string {
  const episodes = status?.episodes_summary;
  if (!episodes || episodes.total === 0) return t("dashboard:lobby_card_no_episodes");
  if (progress === "completed") return t("dashboard:lobby_card_episode_count", { count: episodes.total });
  return t("dashboard:lobby_card_progress", { completed: episodes.completed, total: episodes.total });
}

function updatedText(lastActivityAt: string | null | undefined, lang: string, t: Translate): string | null {
  if (isJustNow(lastActivityAt)) return t("dashboard:lobby_card_updated_just_now");
  const time = formatRelativeTime(lastActivityAt, lang);
  return time ? t("dashboard:lobby_card_updated", { time }) : null;
}
