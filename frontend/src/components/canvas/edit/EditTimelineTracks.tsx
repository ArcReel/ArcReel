import { AlertTriangle } from "lucide-react";
import { memo, useRef, type PointerEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import type { EditClip, EditTimelineReadout } from "@/types/edit-timeline";

import { formatSeconds, rulerStep, unitHue } from "./timeline-view";

const LABEL_WIDTH = 56;

function percentOf(seconds: number, duration: number): string {
  return duration > 0 ? `${(seconds / duration) * 100}%` : "0%";
}
/** 每秒至少占这么宽，长时间线横向滚动而不是把片段挤成细条。 */
const MIN_PIXELS_PER_SECOND = 14;

interface EditTimelineTracksProps {
  projectName: string;
  readout: EditTimelineReadout;
  t: number;
  selectedClipId: string | null;
  activeClipId: string | null;
  trimIgnored: ReadonlySet<string>;
  unusedUnits: readonly string[];
  thumbnails: ReadonlyMap<string, string>;
  onSelectClip: (clipId: string) => void;
  onSeek: (t: number) => void;
}

/** 横向时间线：标尺、视频轨与未使用的视频单元。每条轨道是一个 TrackRow。 */
export function EditTimelineTracks({
  projectName,
  readout,
  t,
  selectedClipId,
  activeClipId,
  trimIgnored,
  unusedUnits,
  thumbnails,
  onSelectClip,
  onSeek,
}: EditTimelineTracksProps) {
  const { t: translate } = useTranslation("dashboard");
  const trackRef = useRef<HTMLDivElement>(null);
  const duration = readout.duration;
  const percent = (seconds: number) => percentOf(seconds, duration);

  const seekFromPointer = (event: PointerEvent) => {
    const box = trackRef.current?.getBoundingClientRect();
    if (!box || box.width <= 0) return;
    onSeek(((event.clientX - box.left) / box.width) * duration);
  };

  return (
    <div className="rounded-[10px] border border-hairline bg-bg-grad-b/60 p-3">
      <div className="overflow-x-auto">
        <div
          className="relative"
          style={{ paddingLeft: LABEL_WIDTH, minWidth: LABEL_WIDTH + duration * MIN_PIXELS_PER_SECOND }}
        >
          <Ruler duration={duration} percent={percent} />
          <div
            ref={trackRef}
            className="relative cursor-pointer"
            onPointerDown={seekFromPointer}
            aria-label={translate("edit_view_track_aria")}
            role="group"
          >
            <TrackRow label={translate("edit_view_track_video")}>
              <VideoClips
                clips={readout.clips}
                duration={duration}
                selectedClipId={selectedClipId}
                activeClipId={activeClipId}
                trimIgnored={trimIgnored}
                onSelectClip={onSelectClip}
              />
            </TrackRow>
            <div
              aria-hidden
              data-testid="edit-playhead"
              className="pointer-events-none absolute -top-1 bottom-0 z-20 w-px bg-text"
              style={{ left: percent(Math.min(t, duration)) }}
            >
              <span className="absolute -left-[4px] -top-1 h-2 w-2 rotate-45 bg-text" />
            </div>
          </div>
        </div>
      </div>

      {unusedUnits.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-hairline-soft pt-2.5 text-[12px] text-text-3">
          <span>{translate("edit_view_unused")}</span>
          {unusedUnits.map((unitId) => {
            const thumbnail = thumbnails.get(unitId);
            return (
              <span
                key={unitId}
                className="inline-flex items-center gap-1.5 rounded-[6px] border border-dashed border-hairline px-1.5 py-0.5"
              >
                {thumbnail && (
                  <img
                    src={API.getFileUrl(projectName, thumbnail)}
                    alt=""
                    className="h-4 w-7 rounded-[2px] object-cover"
                  />
                )}
                {unitId}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Ruler({ duration, percent }: { duration: number; percent: (seconds: number) => string }) {
  const step = rulerStep(duration);
  const ticks = Array.from({ length: Math.floor(duration / step) + 1 }, (_, i) => i * step);
  return (
    <div aria-hidden className="relative mb-1 h-4 text-[10px] tabular-nums text-text-4">
      {ticks.map((tick) => (
        <span key={tick} className="absolute -translate-x-1/2" style={{ left: percent(tick) }}>
          {tick}s
        </span>
      ))}
    </div>
  );
}

function TrackRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="relative h-[58px] border-b border-hairline-soft last:border-b-0">
      <span
        className="absolute top-1/2 -translate-y-1/2 text-[11px] text-text-3"
        style={{ left: -LABEL_WIDTH, width: LABEL_WIDTH - 8 }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

interface VideoClipsProps {
  clips: readonly EditClip[];
  duration: number;
  selectedClipId: string | null;
  activeClipId: string | null;
  trimIgnored: ReadonlySet<string>;
  onSelectClip: (clipId: string) => void;
}

/** 片段宽度与时长成比例；已删除单元的片段时长为 0，画成切点上的红色标记。播放头每帧移动，片段不随之重绘。 */
const VideoClips = memo(function VideoClips({
  clips,
  duration,
  selectedClipId,
  activeClipId,
  trimIgnored,
  onSelectClip,
}: VideoClipsProps) {
  const { t } = useTranslation("dashboard");
  const percent = (seconds: number) => percentOf(seconds, duration);
  const live = clips.filter((clip) => clip.status !== "unit_deleted");
  const lastLive = live[live.length - 1];
  return (
    <>
      {clips.map((clip) =>
        clip.status === "unit_deleted" ? (
          <button
            key={clip.id}
            type="button"
            onClick={() => onSelectClip(clip.id)}
            title={t("edit_view_clip_deleted_marker", { clip: clip.id })}
            aria-label={t("edit_view_clip_deleted_marker", { clip: clip.id })}
            aria-pressed={selectedClipId === clip.id}
            data-testid={`edit-clip-deleted-${clip.id}`}
            className="focus-ring absolute inset-y-0 z-10 flex w-4 -translate-x-1/2 flex-col items-center"
            style={{ left: percent(clip.start) }}
          >
            <span className="h-full w-[2px] bg-danger" />
            <span className="absolute -bottom-1 rounded-[3px] bg-danger px-1 text-[9.5px] leading-[14px] text-black">
              {clip.id}
            </span>
          </button>
        ) : (
          <ClipBlock
            key={clip.id}
            clip={clip}
            left={percent(clip.start)}
            width={percent(clip.duration)}
            selected={selectedClipId === clip.id}
            active={activeClipId === clip.id}
            trimIgnored={trimIgnored.has(clip.id)}
            onSelect={() => onSelectClip(clip.id)}
          />
        ),
      )}
      {live.map(
        (clip) =>
          clip.transition_to_next &&
          clip !== lastLive && (
            <span
              key={`transition-${clip.id}`}
              aria-hidden
              title={`${t(`edit_transition_${clip.transition_to_next.type}`, { defaultValue: clip.transition_to_next.type })} ${formatSeconds(clip.transition_to_next.duration)}s`}
              className="pointer-events-none absolute top-1/2 z-10 h-5 -translate-y-1/2 rounded-[3px] bg-accent/35 ring-1 ring-accent"
              style={{
                left: `calc(${percent(clip.start + clip.duration)} - ${percent(clip.transition_to_next.duration / 2)})`,
                width: percent(clip.transition_to_next.duration),
              }}
            />
          ),
      )}
    </>
  );
});

interface ClipBlockProps {
  clip: EditClip;
  left: string;
  width: string;
  selected: boolean;
  active: boolean;
  trimIgnored: boolean;
  onSelect: () => void;
}

function ClipBlock({ clip, left, width, selected, active, trimIgnored, onSelect }: ClipBlockProps) {
  const { t } = useTranslation("dashboard");
  const missingVideo = clip.status === "video_missing";
  const outline = selected ? "ring-2 ring-text" : active ? "ring-1 ring-accent" : "";
  const border = trimIgnored
    ? "border border-dashed border-warn"
    : missingVideo
      ? "border border-dashed border-hairline-strong"
      : "border border-black/30";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      title={t("edit_view_clip_title", { clip: clip.id, unit: clip.unit_id, duration: formatSeconds(clip.duration) })}
      data-testid={`edit-clip-${clip.id}`}
      data-trim-ignored={trimIgnored || undefined}
      className={`focus-ring absolute inset-y-1.5 overflow-hidden rounded-[5px] text-left ${border} ${outline}`}
      style={{
        left,
        width: `calc(${width} - 2px)`,
        background: missingVideo ? "var(--color-surface-2)" : `oklch(0.42 0.07 ${unitHue(clip.unit_id)})`,
      }}
    >
      <span className="flex h-full flex-col justify-between p-1 text-[10.5px] leading-none text-white">
        <span className="flex items-center gap-1 whitespace-nowrap">
          <b>{clip.id}</b>
          <span className="opacity-80">{clip.unit_id}</span>
          {trimIgnored && <AlertTriangle aria-hidden className="h-3 w-3 shrink-0 text-warn" />}
        </span>
        <span className="tabular-nums opacity-75">{formatSeconds(clip.duration)}s</span>
      </span>
    </button>
  );
}
