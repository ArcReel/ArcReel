import type { CSSProperties } from "react";
import { cn } from "cn";
import { AlertTriangle, Captions, Loader2, Pause, Play } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import type { EditClip } from "@/types/edit-timeline";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import type { PreviewAspect } from "@/utils/preview-aspect";

import { transitionOpacity, type PlaybackPlan } from "./playback-schedule";
import { subtitleAt, subtitleLayout, type PlacedSubtitle } from "./preview-tracks";
import { formatClock } from "./timeline-view";
import type { TimelinePlayback } from "./useTimelinePlayback";

interface EditTimelinePlayerProps {
  plan: PlaybackPlan;
  playback: TimelinePlayback;
  aspect: PreviewAspect;
  /** 当前片段；定格与占位期间同样有值，没有可播放片段时为 undefined。 */
  current: EditClip | undefined;
  trimIgnored: boolean;
  subtitles: readonly PlacedSubtitle[];
  showSubtitles: boolean;
  onToggleSubtitles: () => void;
}

/**
 * 播放画面与播放控制：两个 `<video>` 叠放，只显示当前那一个；字幕按剪映草稿的样式比例叠在画面上。
 * 画面按所在格子的宽高缩放：舞台是尺寸容器，画框取「容器宽」与「容器高 × 画幅比」中较小的一个。
 */
export function EditTimelinePlayer({
  plan,
  playback,
  aspect,
  current,
  trimIgnored,
  subtitles,
  showSubtitles,
  onToggleSubtitles,
}: EditTimelinePlayerProps) {
  const { t } = useTranslation("dashboard");
  const segment = plan.segments[playback.index];
  const showVideo = Boolean(segment?.hasVideo);
  const opacity = segment ? transitionOpacity(segment, playback.t) : 1;
  const hasTransitions = plan.segments.some((item) => item.fadeOut > 0);
  const subtitle = showSubtitles ? subtitleAt(subtitles, playback.t) : null;
  const layout = subtitleLayout(aspect);
  const portrait = aspect === "9:16";

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="@container-size relative flex min-h-0 flex-1 items-center justify-center">
        <div
          role="region"
          aria-label={t("edit_view_player_aria")}
          className={cn(
            "@container-size relative overflow-hidden rounded-lg bg-black",
            portrait
              ? "aspect-[9/16] w-[min(100cqw,calc(100cqh*9/16))]"
              : "aspect-video w-[min(100cqw,calc(100cqh*16/9))]",
          )}
        >
          {playback.videoRefs.map((ref, slot) => {
            const visible = showVideo && playback.visibleSlot === slot;
            return (
              // eslint-disable-next-line jsx-a11y/media-has-caption -- 字幕由剪辑时间线的字幕轨叠加显示，不走 <track>
              <video
                key={slot}
                ref={ref}
                playsInline
                preload="auto"
                data-testid={`edit-player-video-${slot}`}
                className="absolute inset-0 size-full object-contain opacity-(--stage-opacity)"
                style={{ "--stage-opacity": visible ? opacity : 0 } as CSSProperties}
              />
            );
          })}
          {segment && !segment.hasVideo && (
            <div
              className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground opacity-(--stage-opacity)"
              style={{ "--stage-opacity": opacity } as CSSProperties}
            >
              {t("edit_view_stage_video_missing")}
            </div>
          )}
          {subtitle && (
            <p
              data-testid="edit-player-subtitle"
              className="pointer-events-none absolute top-(--subtitle-top) left-1/2 m-0 w-(--subtitle-width) -translate-x-1/2 -translate-y-1/2 text-center burned-subtitle"
              style={
                {
                  "--subtitle-top": `${layout.centerFromTopPercent}%`,
                  "--subtitle-width": `${layout.maxWidthPercent}%`,
                  "--subtitle-size": `${layout.fontSizePercentOfShortSide}cqmin`,
                } as CSSProperties
              }
            >
              {subtitle.text}
            </p>
          )}
          {current && (
            <div className="pointer-events-none absolute inset-x-2.5 top-2.5 flex flex-wrap gap-1.5 text-xs">
              <span className="max-w-full truncate rounded-sm bg-black/60 px-1.5 py-0.5 text-white tabular-nums">
                {current.id} · {itemIdWithinEpisode(current.unit_id)}
              </span>
              {trimIgnored && (
                <span className="inline-flex max-w-full items-center gap-1 rounded-sm bg-warn px-1.5 py-0.5 text-black">
                  <AlertTriangle aria-hidden className="size-3 shrink-0" />
                  <span className="truncate">{t("edit_view_stage_trim_ignored")}</span>
                </span>
              )}
            </div>
          )}
          {playback.buffering && (
            <span
              role="status"
              className="pointer-events-none absolute top-2.5 right-2.5 inline-flex items-center gap-1 rounded-sm bg-black/60 px-1.5 py-0.5 text-xs text-white"
            >
              <Loader2 aria-hidden className="size-3 animate-spin" />
              {t("edit_view_buffering")}
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Button
          size="icon-sm"
          onClick={playback.toggle}
          disabled={plan.segments.length === 0}
          aria-label={playback.playing ? t("edit_view_pause") : t("edit_view_play")}
        >
          {playback.playing ? <Pause aria-hidden /> : <Play aria-hidden />}
        </Button>
        <span className="text-sm text-subtle-foreground tabular-nums">
          <span data-testid="edit-playback-clock">{formatClock(playback.t)}</span>{" "}
          <span className="text-muted-foreground">/ {formatClock(plan.duration)}</span>
        </span>
        <span className="text-xs text-muted-foreground">
          {t("edit_view_clip_count", { count: plan.segments.length })}
        </span>
        <Toggle size="sm" pressed={showSubtitles} onPressedChange={onToggleSubtitles} className="ml-auto">
          <Captions data-icon="inline-start" aria-hidden />
          {t("edit_view_subtitles_toggle")}
        </Toggle>
      </div>
      {(playback.blocked || hasTransitions) && (
        <div className="flex shrink-0 flex-col gap-1 text-xs">
          {playback.blocked && (
            <p role="status" className="m-0 text-warn">
              {t("edit_view_playback_blocked")}
            </p>
          )}
          {hasTransitions && <p className="m-0 text-muted-foreground">{t("edit_view_transition_note")}</p>}
        </div>
      )}
    </div>
  );
}
