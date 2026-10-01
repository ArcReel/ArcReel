import { AlertTriangle, Captions, Loader2, Pause, Play } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { EditClip } from "@/types/edit-timeline";
import type { PreviewAspect } from "@/utils/preview-aspect";

import { transitionOpacity, type PlaybackPlan } from "./playback-schedule";
import { subtitleAt, subtitleLayout, type PlacedSubtitle } from "./preview-tracks";
import { formatClock } from "./timeline-view";
import type { TimelinePlayback } from "./useTimelinePlayback";

/** 系统自带的中文黑体，近似成片字幕用的思源黑体粗体。 */
const SUBTITLE_FONT =
  '"PingFang SC", "Hiragino Sans GB", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif';

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

/** 播放画面与播放控制：两个 `<video>` 叠放，只显示当前那一个；字幕按剪映草稿的样式比例叠在画面上。 */
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
  const frame =
    aspect === "9:16"
      ? "aspect-[9/16] h-[min(56vh,520px)]"
      : "aspect-video w-full max-w-[760px]";

  return (
    <div className="flex flex-col items-center gap-2.5">
      <div
        className={`relative overflow-hidden rounded-[10px] bg-black ${frame}`}
        style={{ containerType: "size" }}
        role="region"
        aria-label={t("edit_view_player_aria")}
      >
        {playback.videoRefs.map((ref, slot) => (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- 字幕由剪辑时间线的字幕轨叠加显示，不走 <track>
          <video
            key={slot}
            ref={ref}
            playsInline
            preload="auto"
            data-testid={`edit-player-video-${slot}`}
            className="absolute inset-0 h-full w-full object-contain"
            style={{ opacity: showVideo && playback.visibleSlot === slot ? opacity : 0 }}
          />
        ))}
        {segment && !segment.hasVideo && (
          <div
            className="absolute inset-0 flex items-center justify-center text-[13px] text-text-3"
            style={{ opacity }}
          >
            {t("edit_view_stage_video_missing")}
          </div>
        )}
        {subtitle && (
          <p
            data-testid="edit-player-subtitle"
            className="pointer-events-none absolute left-1/2 m-0 -translate-x-1/2 -translate-y-1/2 text-center font-bold leading-[1.25] text-white"
            style={{
              top: `${layout.centerFromTopPercent}%`,
              width: `${layout.maxWidthPercent}%`,
              fontFamily: SUBTITLE_FONT,
              fontSize: `${layout.fontSizePercentOfShortSide}cqmin`,
              WebkitTextStroke: "0.12em black",
              paintOrder: "stroke fill",
              textShadow: "0.04em 0.06em 0.2em rgb(0 0 0 / 0.7)",
              overflowWrap: "anywhere",
            }}
          >
            {subtitle.text}
          </p>
        )}
        {current && (
          <div className="pointer-events-none absolute left-2.5 top-2.5 flex flex-wrap gap-1.5 text-[11px]">
            <span className="rounded-[4px] bg-black/60 px-1.5 py-0.5 tabular-nums text-white/90">
              {current.id} · {current.unit_id}
            </span>
            {trimIgnored && (
              <span className="inline-flex items-center gap-1 rounded-[4px] bg-warn/90 px-1.5 py-0.5 text-black">
                <AlertTriangle aria-hidden className="h-3 w-3" />
                {t("edit_view_stage_trim_ignored")}
              </span>
            )}
          </div>
        )}
        {playback.buffering && (
          <span
            role="status"
            className="pointer-events-none absolute right-2.5 top-2.5 inline-flex items-center gap-1 rounded-[4px] bg-black/60 px-1.5 py-0.5 text-[11px] text-white/90"
          >
            <Loader2 aria-hidden className="h-3 w-3 animate-spin motion-reduce:animate-none" />
            {t("edit_view_buffering")}
          </span>
        )}
      </div>
      <div className="flex w-full max-w-[760px] items-center gap-3">
        <button
          type="button"
          onClick={playback.toggle}
          disabled={plan.segments.length === 0}
          aria-label={playback.playing ? t("edit_view_pause") : t("edit_view_play")}
          className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-text text-bg transition-opacity hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {playback.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 translate-x-px" />}
        </button>
        <span className="text-[13px] tabular-nums text-text-2">
          {formatClock(playback.t)} <span className="text-text-4">/ {formatClock(plan.duration)}</span>
        </span>
        <span className="text-[12px] text-text-4">
          {t("edit_view_clip_count", { count: plan.segments.length })}
        </span>
        <button
          type="button"
          onClick={onToggleSubtitles}
          aria-pressed={showSubtitles}
          className={`focus-ring ml-auto inline-flex items-center gap-1.5 rounded-[7px] px-2 py-1 text-[12px] transition-colors ${
            showSubtitles ? "bg-accent-dim text-text" : "text-text-3 hover:text-text"
          }`}
        >
          <Captions aria-hidden className="h-3.5 w-3.5" />
          {t("edit_view_subtitles_toggle")}
        </button>
      </div>
      {(playback.blocked || hasTransitions) && (
        <div className="flex w-full max-w-[760px] flex-col gap-1 text-[12px]">
          {playback.blocked && (
            <p role="status" className="m-0 text-warn">
              {t("edit_view_playback_blocked")}
            </p>
          )}
          {hasTransitions && <p className="m-0 text-text-4">{t("edit_view_transition_note")}</p>}
        </div>
      )}
    </div>
  );
}
