import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";
import { cn } from "cn";
import type { EpisodeMeta } from "@/types";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Progress } from "@/components/ui/progress";
import { itemCountKey, type GenerationRoute } from "@/utils/generation-mode";
import { useCostStore } from "@/stores/cost-store";
import { totalBreakdown } from "@/utils/cost-format";

interface EpisodeCardProps {
  ep: EpisodeMeta;
  /** 在播出顺序（episodes[] 排列）中的位置，从 1 起；徽标显示它而不是集 ID。 */
  position: number;
  active: boolean;
  onClick: () => void;
  /** ad 项目隐藏集语义：徽标不显示播出位置，改用场记板图标。 */
  showEpisodeBadge?: boolean;
  /** ep.title 为空时的兜底显示文本（ad 项目用项目标题）。 */
  fallbackTitle?: string;
  /** 项目生成模式：决定条目数报「分镜数」还是「视频单元数」。必填，漏接线时类型报错而不是静默显示错名词。 */
  route: GenerationRoute;
}

const STATUS_DOT: Record<string, string> = {
  completed: "bg-good",
  in_production: "animate-breath bg-primary",
  scripted: "bg-subtle-foreground",
  draft: "bg-muted-foreground",
  missing: "bg-muted-foreground",
};

const STATUS_LABEL_KEY: Record<string, string> = {
  completed: "dashboard:episode_status_done",
  in_production: "dashboard:episode_status_active",
  scripted: "dashboard:episode_status_draft",
  draft: "dashboard:episode_status_draft",
  missing: "dashboard:episode_status_idea",
};

/**
 * 侧栏的集卡片：左边是播出位置，中间是集名、状态与进度，右边是实际费用。当前打开的集带底色。
 */
export function EpisodeCard({
  ep,
  position,
  active,
  onClick,
  showEpisodeBadge = true,
  fallbackTitle,
  route,
}: EpisodeCardProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const status = ep.status ?? "draft";
  const statusLabel = t(STATUS_LABEL_KEY[status] ?? STATUS_LABEL_KEY.draft);

  // 进度按视频产物的可用数算——可用 = current ∪ stale，与工作台同一份计数。
  // 视频总数为 0（尚未成脚本）时退回剧本条目数，只用于显示"这集有几件内容"。
  const videoTotal = ep.videos?.total ?? 0;
  const itemCount = ep.item_count ?? 0;
  const totalShots = videoTotal || itemCount;
  const availableVideos = ep.videos?.available ?? 0;
  const progress = videoTotal > 0 ? Math.round((availableVideos / videoTotal) * 100) : 0;
  const showProgress = videoTotal > 0 && (active || progress > 0);

  // stale 是可用产物，不进缺口计数：单独报一个数说明有几件可以考虑重生。
  // 汇总该集全部产物类型，与大厅卡片上那一行同口径。
  const staleCount = (ep.storyboards?.stale ?? 0) + (ep.videos?.stale ?? 0);

  const episodeCost = useCostStore((s) => s.getEpisodeCost(ep.episode));
  const spentBreakdown = episodeCost ? totalBreakdown(episodeCost.totals.actual) : null;
  const spentEntries = spentBreakdown ? Object.entries(spentBreakdown).filter(([, v]) => v > 0) : [];
  const primaryCost = spentEntries.find(([c]) => c === "USD") ?? spentEntries[0];
  const costText = primaryCost ? `${primaryCost[0] === "CNY" ? "¥" : "$"}${primaryCost[1].toFixed(2)}` : null;

  const dur = ep.duration_seconds ?? 0;
  const durLabel = dur > 0 ? `${Math.floor(dur / 60)}:${String(dur % 60).padStart(2, "0")}` : null;
  const title = ep.title || fallbackTitle || t("common:episode_position_name", { position });

  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 rounded-md p-2 text-left transition-colors focus-ring",
        active ? "bg-primary/10" : "hover:bg-muted",
      )}
    >
      <span
        className={cn(
          "num grid size-8 shrink-0 place-items-center rounded-md text-xs font-medium",
          active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
        )}
      >
        {showEpisodeBadge ? position : <Clapperboard aria-hidden className="size-4" />}
      </span>

      <span className="flex min-w-0 flex-col gap-0.5">
        <TruncatedText
          text={title}
          focusable={false}
          className={cn("text-sm", active ? "font-medium text-foreground" : "text-subtle-foreground")}
        />
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status] ?? STATUS_DOT.draft)} />
            {statusLabel}
          </span>
          {totalShots > 0 && (
            <span className="num">
              {videoTotal > 0 ? (
                <>
                  <span aria-hidden>{`${availableVideos}/${videoTotal}`}</span>
                  <span className="sr-only">
                    {t("episode_available_videos_hint", { count: availableVideos, total: videoTotal })}
                  </span>
                </>
              ) : (
                t(itemCountKey(route), { count: itemCount })
              )}
              {durLabel ? ` · ${durLabel}` : ""}
            </span>
          )}
          {staleCount > 0 && (
            <span className="num inline-flex items-center gap-1 text-warn">
              <span aria-hidden className="size-1.5 rounded-full bg-warn" />
              <span aria-hidden>{staleCount}</span>
              <span className="sr-only">{t("episode_stale_artifacts", { count: staleCount })}</span>
            </span>
          )}
        </span>
        {showProgress && <Progress value={progress} aria-hidden className="mt-1" />}
      </span>

      {costText && (
        <span className={cn("num self-start text-xs", active ? "text-primary" : "text-muted-foreground")}>
          {costText}
        </span>
      )}
    </button>
  );
}
