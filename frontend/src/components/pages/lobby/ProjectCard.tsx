/**
 * Lobby project poster cards, also used readonly for onboarding demos: demonstrations of advanced project appearances remain truthful over time only
 * with shared implementations. Readonly cards open readonly workbenches, without More menus.
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
import { API_BASE } from "@/api/transport";
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

/** Real lobby cards require `actions`; readonly demo cards reject them. */
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
          {readOnly && <span className="sr-only">{t("onboarding:demo_badge")}</span>}
        </div>
      </Link>

      {actions && (
        // Hover-capable devices hide controls until hover/focus/open menus; touch devices always show them.
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

const FILES_URL_PREFIX = `${API_BASE}/files/`;

/**
 * Request 640-wide thumbnails for project-file covers: cards fill columns starting at 280px; posters usually stay below 400px wide.
 * Backend URLs may already contain `?v=`; return other sources such as demo data: unchanged.
 */
function posterSrc(url: string): string {
  if (!url.startsWith(FILES_URL_PREFIX)) return url;
  return `${url}${url.includes("?") ? "&" : "?"}w=640`;
}

/** Posters use 2:1. Without thumbnails, render serif titles rather than meaningless color blocks. */
function Poster({ project, title }: { project: ProjectSummary; title: string }) {
  return (
    <div className="relative aspect-2/1 overflow-hidden bg-linear-to-br from-primary/30 via-muted to-background">
      {project.thumbnail ? (
        <img
          src={posterSrc(project.thumbnail)}
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

/** One cell per episode: completed, producing, scripted, unstarted. Counts appear in secondary text below; these are illustrative only. */
function EpisodeStrip({ summary }: { summary: ProjectStatus["episodes_summary"] | undefined }) {
  if (!summary || summary.total === 0) return null;
  const inProductionEnd = summary.completed + summary.in_production;
  // `scripted` counts scripted episodes, including completed and producing episodes.
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
