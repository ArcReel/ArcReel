import { AlertTriangle, Loader2, Upload } from "lucide-react";
import { memo, useRef, useState, type ChangeEvent, type PointerEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { EditClip, EditTimelineReadout } from "@/types/edit-timeline";

import type { AudioPlacement, NarrationSpan, PlacedSubtitle } from "./preview-tracks";
import { formatSeconds, rulerStep, unitHue } from "./timeline-view";
import { itemIdWithinEpisode } from "@/utils/episode-display";

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
  narration: readonly NarrationSpan[];
  subtitles: readonly PlacedSubtitle[];
  bgm: readonly AudioPlacement[];
  onSelectClip: (clipId: string) => void;
  onSeek: (t: number) => void;
}

/** 横向时间线：标尺，视频、旁白、字幕与 BGM 四条轨道，以及未使用的视频单元。每条轨道是一个 TrackRow；BGM 轨带上传入口。 */
export function EditTimelineTracks({
  projectName,
  readout,
  t,
  selectedClipId,
  activeClipId,
  trimIgnored,
  unusedUnits,
  thumbnails,
  narration,
  subtitles,
  bgm,
  onSelectClip,
  onSeek,
}: EditTimelineTracksProps) {
  const { t: translate } = useTranslation("dashboard");
  const trackRef = useRef<HTMLDivElement>(null);
  const duration = readout.duration;
  const percent = (seconds: number) => percentOf(seconds, duration);

  const seekFromPointer = (event: PointerEvent) => {
    if (event.button !== 0) return;
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
            <TrackRow label={translate("edit_view_track_narration")} height="h-[40px]">
              <NarrationBlocks spans={narration} duration={duration} />
            </TrackRow>
            <TrackRow label={translate("edit_view_track_subtitles")} height="h-[30px]">
              <SubtitleBlocks subtitles={subtitles} duration={duration} />
            </TrackRow>
            <TrackRow
              label={translate("edit_view_track_bgm")}
              height="h-[30px]"
              action={<BgmUploadButton projectName={projectName} />}
            >
              {bgm.length > 0 ? (
                <BgmBlocks items={bgm} duration={duration} />
              ) : (
                <span className="absolute inset-y-0 left-1 flex items-center text-[10.5px] text-text-4">
                  {translate("edit_view_bgm_track_empty")}
                </span>
              )}
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
                {itemIdWithinEpisode(unitId)}
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

interface TrackRowProps {
  label: string;
  height?: string;
  /** 轨道名旁的操作按钮。 */
  action?: ReactNode;
  children: ReactNode;
}

function TrackRow({ label, height = "h-[58px]", action, children }: TrackRowProps) {
  return (
    <div className={`relative ${height} border-b border-hairline-soft last:border-b-0`}>
      <span
        className="absolute top-1/2 flex -translate-y-1/2 items-center gap-1 text-[11px] text-text-3"
        style={{ left: -LABEL_WIDTH, width: LABEL_WIDTH - 8 }}
      >
        {label}
        {action}
      </span>
      {children}
    </div>
  );
}

const BGM_ACCEPT = ".mp3,.wav,.m4a,audio/mpeg,audio/wav,audio/mp4";

/** 上传一首 BGM 到项目。按下按钮不触发轨道的跳转；上传后由 Agent 把 BGM 摆进剪辑时间线。 */
function BgmUploadButton({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    const { pushToast } = useAppStore.getState();
    try {
      const { bgm } = await API.uploadBgm(projectName, file);
      pushToast(t("edit_view_bgm_uploaded", { name: bgm.name }), "success");
    } catch (cause) {
      pushToast(t("edit_view_bgm_upload_failed", { message: errMsg(cause) }), "error");
    } finally {
      setUploading(false);
    }
  };
  const label = uploading ? t("edit_view_bgm_uploading") : t("edit_view_bgm_upload");
  return (
    <>
      <button
        type="button"
        title={label}
        aria-label={label}
        disabled={uploading}
        data-testid="edit-bgm-upload"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => input.current?.click()}
        className="focus-ring inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] text-text-3 hover:bg-bg-grad-b hover:text-text disabled:opacity-60"
      >
        {uploading ? <Loader2 aria-hidden className="h-3 w-3 animate-spin" /> : <Upload aria-hidden className="h-3 w-3" />}
      </button>
      <input
        ref={input}
        type="file"
        accept={BGM_ACCEPT}
        hidden
        data-testid="edit-bgm-upload-input"
        onChange={(event) => void upload(event)}
      />
    </>
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
      title={t("edit_view_clip_title", {
        clip: clip.id,
        unit: itemIdWithinEpisode(clip.unit_id),
        duration: formatSeconds(clip.duration),
      })}
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
          <span className="opacity-80">{itemIdWithinEpisode(clip.unit_id)}</span>
          {trimIgnored && <AlertTriangle aria-hidden className="h-3 w-3 shrink-0 text-warn" />}
        </span>
        <span className="tabular-nums opacity-75">{formatSeconds(clip.duration)}s</span>
      </span>
    </button>
  );
}

/**
 * 旁白按实际起止画在轨上，可以越过承载片段；重叠的旁白分两行。TTS 项目没有旁白配音的按承载片段占位，画成虚线；
 * 后期配音项目的旁白画成中性占位，提示由后期配音、预览不出声。
 */
const NarrationBlocks = memo(function NarrationBlocks({
  spans,
  duration,
}: {
  spans: readonly NarrationSpan[];
  duration: number;
}) {
  const { t } = useTranslation("dashboard");
  const lanes = Math.max(1, ...spans.map((span) => span.lane + 1));
  return (
    <>
      {spans.map((span) => {
        const title = span.postProduction
          ? t("edit_view_narration_post_production", { unit: itemIdWithinEpisode(span.unitId), clip: span.clipId })
          : span.missingAudio
          ? t("edit_view_narration_missing", { unit: itemIdWithinEpisode(span.unitId), clip: span.clipId })
          : t("edit_view_narration_title", {
              unit: itemIdWithinEpisode(span.unitId),
              clip: span.clipId,
              start: formatSeconds(span.start),
              end: formatSeconds(span.end),
            });
        return (
          <span
            key={span.clipId}
            title={title}
            data-testid={`edit-narration-${span.clipId}`}
            data-missing-audio={span.missingAudio || undefined}
            data-post-production={span.postProduction || undefined}
            className={`absolute flex items-center overflow-hidden whitespace-nowrap rounded-[4px] px-1 text-[10px] leading-none ${
              span.postProduction
                ? "border border-hairline bg-surface-2 text-text-3"
                : span.missingAudio
                  ? "border border-dashed border-hairline-strong text-text-3"
                  : "border border-black/30 text-white"
            }`}
            style={{
              left: percentOf(span.start, duration),
              width: `calc(${percentOf(span.end - span.start, duration)} - 2px)`,
              top: `calc(${(span.lane / lanes) * 100}% + 4px)`,
              height: `calc(${100 / lanes}% - 8px)`,
              background: span.postProduction
                ? undefined
                : span.missingAudio
                  ? "transparent"
                  : `oklch(0.5 0.08 ${unitHue(span.unitId)} / 0.75)`,
            }}
          >
            {itemIdWithinEpisode(span.unitId)}
          </span>
        );
      })}
    </>
  );
});

const SubtitleBlocks = memo(function SubtitleBlocks({
  subtitles,
  duration,
}: {
  subtitles: readonly PlacedSubtitle[];
  duration: number;
}) {
  return (
    <>
      {subtitles.map((item) => (
        <span
          key={`${item.start}-${item.text}`}
          title={item.text}
          className="absolute inset-y-1 flex items-center overflow-hidden whitespace-nowrap rounded-[3px] border border-hairline bg-surface-2 px-1 text-[10px] leading-none text-text-2"
          style={{
            left: percentOf(item.start, duration),
            width: `calc(${percentOf(item.end - item.start, duration)} - 1px)`,
          }}
        >
          {item.text}
        </span>
      ))}
    </>
  );
});

/** BGM 片段的淡入淡出画成两端的渐变。 */
const BgmBlocks = memo(function BgmBlocks({ items, duration }: { items: readonly AudioPlacement[]; duration: number }) {
  const { t } = useTranslation("dashboard");
  const tone = (alpha: number) => `oklch(0.45 0.09 300 / ${alpha})`;
  return (
    <>
      {items.map((item) => {
        const length = item.end - item.start;
        const fadeIn = (Math.min(item.fadeIn, length) / length) * 100;
        const fadeOut = 100 - (Math.min(item.fadeOut, length) / length) * 100;
        return (
          <span
            key={item.id}
            title={t("edit_view_bgm_title", {
              bgm: item.name ?? item.sourceId,
              start: formatSeconds(item.start),
              end: formatSeconds(item.end),
              volume: formatSeconds(item.volume),
              fadeIn: formatSeconds(item.fadeIn),
              fadeOut: formatSeconds(item.fadeOut),
            })}
            data-testid={`edit-${item.id}`}
            className="absolute inset-y-1 flex items-center overflow-hidden whitespace-nowrap rounded-[3px] px-1 text-[10px] leading-none text-white"
            style={{
              left: percentOf(item.start, duration),
              width: `calc(${percentOf(length, duration)} - 1px)`,
              background: `linear-gradient(90deg, ${tone(0.25)}, ${tone(0.85)} ${fadeIn}%, ${tone(0.85)} ${fadeOut}%, ${tone(0.25)})`,
            }}
          >
            {item.name ?? item.sourceId}
          </span>
        );
      })}
    </>
  );
});
