import { cn } from "cn";
import { AlertTriangle, Loader2, Upload } from "lucide-react";
import { memo, useRef, useState, type ChangeEvent, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { EditClip, EditTimelineReadout } from "@/types/edit-timeline";

import type { AudioPlacement, NarrationSpan, PlacedSubtitle } from "./preview-tracks";
import { formatSeconds, rulerStep, unitHue } from "./timeline-view";
import { itemIdWithinEpisode } from "@/utils/episode-display";

function percentOf(seconds: number, duration: number): string {
  return duration > 0 ? `${(seconds / duration) * 100}%` : "0%";
}
/** 每秒至少占这么宽，长时间线横向滚动而不是把片段挤成细条。 */
const MIN_PIXELS_PER_SECOND = 14;
/** 轨道名一列的宽度，与下方 `pl-16` / `-left-16` 一致。 */
const LABEL_WIDTH = 64;


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

/**
 * 横向时间线：标尺，视频、旁白、字幕与 BGM 四条轨道，以及未使用的视频单元。每条轨道是一个 TrackRow；BGM 轨带上传入口。
 * 高度固定、不随条目增多而变高：长时间线与很多未使用的视频单元都在各自一行里横向滚动。
 */
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
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-sidebar/60 p-3 edit-short:p-2">
      <div className="relative overflow-x-auto scroll-fade-x">
        <div
          className="relative min-w-(--track-min-width) pl-16"
          style={{ "--track-min-width": `${LABEL_WIDTH + duration * MIN_PIXELS_PER_SECOND}px` } as CSSProperties}
        >
          <Ruler duration={duration} percent={percent} />
          <div
            ref={trackRef}
            className="relative cursor-pointer"
            onPointerDown={seekFromPointer}
            aria-label={translate("edit_view_track_aria")}
            role="group"
          >
            <TrackRow label={translate("edit_view_track_video")} height="h-14 edit-short:h-10">
              <VideoClips
                clips={readout.clips}
                duration={duration}
                selectedClipId={selectedClipId}
                activeClipId={activeClipId}
                trimIgnored={trimIgnored}
                onSelectClip={onSelectClip}
              />
            </TrackRow>
            <TrackRow label={translate("edit_view_track_narration")} height="h-10 edit-short:h-7">
              <NarrationBlocks spans={narration} duration={duration} />
            </TrackRow>
            <TrackRow label={translate("edit_view_track_subtitles")} height="h-8 edit-short:h-6">
              <SubtitleBlocks subtitles={subtitles} duration={duration} />
            </TrackRow>
            <TrackRow
              label={translate("edit_view_track_bgm")}
              height="h-8 edit-short:h-6"
              action={<BgmUploadButton projectName={projectName} />}
            >
              {bgm.length > 0 ? (
                <BgmBlocks items={bgm} duration={duration} />
              ) : (
                <span className="absolute inset-y-0 left-1 flex items-center text-xs text-muted-foreground">
                  {translate("edit_view_bgm_track_empty")}
                </span>
              )}
            </TrackRow>
            <div
              aria-hidden
              data-testid="edit-playhead"
              className="pointer-events-none absolute -top-1 bottom-0 left-(--at) w-px bg-foreground"
              style={{ "--at": percent(Math.min(t, duration)) } as CSSProperties}
            >
              <span className="absolute -top-1 -left-1 size-2 rotate-45 bg-foreground" />
            </div>
          </div>
        </div>
      </div>

      {/* 视图矮时不显示这一行：未使用的视频单元同时列在详情栏的问题里 */}
      {unusedUnits.length > 0 && (
        <div
          role="region"
          aria-label={translate("edit_view_unused")}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 条目多时这一行横向滚动，里面没有可聚焦的元素，键盘要能聚焦后滚动
          tabIndex={0}
          className="focus-ring relative flex items-center gap-2 overflow-x-auto border-t edit-short:hidden border-border/50 pt-2 text-xs whitespace-nowrap text-muted-foreground scroll-fade-x">
          <span className="shrink-0">{translate("edit_view_unused")}</span>
          {unusedUnits.map((unitId) => {
            const thumbnail = thumbnails.get(unitId);
            return (
              <span
                key={unitId}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-dashed border-border px-1.5 py-0.5"
              >
                {thumbnail && <UnusedUnitThumbnail projectName={projectName} path={thumbnail} />}
                {itemIdWithinEpisode(unitId)}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 未使用单元的小缩略图；单独订阅这张图的指纹，指纹变化只重渲这一张。 */
function UnusedUnitThumbnail({ projectName, path }: { projectName: string; path: string }) {
  const fingerprint = useProjectsStore((s) => s.getAssetFingerprint(path));
  return (
    <img
      src={API.getFileUrl(projectName, path, fingerprint, { width: 160 })}
      alt=""
      loading="lazy"
      decoding="async"
      className="h-4 w-7 rounded-xs object-cover"
    />
  );
}

function Ruler({ duration, percent }: { duration: number; percent: (seconds: number) => string }) {
  const { t } = useTranslation("dashboard");
  const step = rulerStep(duration);
  const ticks = Array.from({ length: Math.floor(duration / step) + 1 }, (_, i) => i * step);
  return (
    <div aria-hidden className="relative mb-1 h-4 text-xs text-muted-foreground tabular-nums">
      {ticks.map((tick) => (
        <span
          key={tick}
          className="absolute left-(--at) -translate-x-1/2 leading-4"
          style={{ "--at": percent(tick) } as CSSProperties}
        >
          {t("edit_view_seconds", { value: tick })}
        </span>
      ))}
    </div>
  );
}

interface TrackRowProps {
  label: string;
  /** 轨道高度的类名，如 `h-10`。 */
  height: string;
  /** 轨道名旁的操作按钮。 */
  action?: ReactNode;
  children: ReactNode;
}

function TrackRow({ label, height, action, children }: TrackRowProps) {
  return (
    <div className={cn("relative border-b border-border/50 last:border-b-0", height)}>
      <span className="absolute top-1/2 -left-16 flex w-14 -translate-y-1/2 items-center gap-0.5 text-xs text-muted-foreground">
        <span className="min-w-0 truncate">{label}</span>
        {action}
      </span>
      {children}
    </div>
  );
}

const BGM_ACCEPT = ".mp3,.wav,.m4a,audio/mpeg,audio/wav,audio/mp4";

/**
 * 上传一首 BGM 到项目。按下按钮不触发轨道的跳转；上传后由 Agent 把 BGM 摆进剪辑时间线。
 * 上传成功后界面上没有别的变化，所以仍用提示告诉创作者下一步。
 */
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
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={label}
        title={label}
        disabled={uploading}
        data-testid="edit-bgm-upload"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => input.current?.click()}
      >
        {uploading ? <Loader2 aria-hidden className="animate-spin" /> : <Upload aria-hidden />}
      </Button>
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

/**
 * 片段宽度与时长成比例；已删除单元的片段时长为 0，画成切点上的红色标记。播放头每帧移动，片段不随之重绘。
 * 叠放靠渲染顺序，不写 z-index：片段在下，转场与删除标记在上，播放头最后渲染、盖在最上面。
 */
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
  const deleted = clips.filter((clip) => clip.status === "unit_deleted");
  const lastLive = live[live.length - 1];
  return (
    <>
      {live.map((clip) => (
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
      ))}
      {live.map(
        (clip) =>
          clip.transition_to_next &&
          clip !== lastLive && (
            <span
              key={`transition-${clip.id}`}
              aria-hidden
              title={t("edit_view_transition_marker", {
                type: t(`edit_transition_${clip.transition_to_next.type}`, { defaultValue: clip.transition_to_next.type }),
                duration: formatSeconds(clip.transition_to_next.duration),
              })}
              className="pointer-events-none absolute top-1/2 left-[calc(var(--at)-var(--span)/2)] h-5 w-(--span) -translate-y-1/2 rounded-xs bg-primary/35 ring-1 ring-primary"
              style={
                {
                  "--at": percent(clip.start + clip.duration),
                  "--span": percent(clip.transition_to_next.duration),
                } as CSSProperties
              }
            />
          ),
      )}
      {deleted.map((clip) => (
        <button
          key={clip.id}
          type="button"
          onClick={() => onSelectClip(clip.id)}
          title={t("edit_view_clip_deleted_marker", { clip: clip.id })}
          aria-label={t("edit_view_clip_deleted_marker", { clip: clip.id })}
          aria-pressed={selectedClipId === clip.id}
          data-testid={`edit-clip-deleted-${clip.id}`}
          className="focus-ring absolute inset-y-0 left-(--at) flex w-6 -translate-x-1/2 flex-col items-center"
          style={{ "--at": percent(clip.start) } as CSSProperties}
        >
          <span className="h-full w-0.5 bg-destructive" />
          <span className="absolute -bottom-1 rounded-xs bg-destructive px-1 text-xs leading-3.5 text-black">
            {clip.id}
          </span>
        </button>
      ))}
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
      className={cn(
        "focus-ring absolute inset-y-1.5 left-(--at) w-[calc(var(--span)-2px)] overflow-hidden text-ellipsis rounded-sm border text-left",
        trimIgnored
          ? "border-dashed border-warn"
          : missingVideo
            ? "border-dashed border-input"
            : "border-black/30",
        missingVideo ? "bg-muted" : "bg-unit-clip",
        selected ? "ring-2 ring-foreground" : active && "ring-1 ring-primary",
      )}
      style={{ "--at": left, "--span": width, "--unit-hue": unitHue(clip.unit_id) } as CSSProperties}
    >
      <span className="flex h-full flex-col justify-between p-1 text-xs leading-none text-white">
        <span className="flex items-center gap-1 whitespace-nowrap">
          <b>{clip.id}</b>
          <span>{itemIdWithinEpisode(clip.unit_id)}</span>
          {trimIgnored && <AlertTriangle aria-hidden className="size-3 shrink-0 text-warn" />}
        </span>
        {/* 视图矮时轨道变矮，只留第一行；时长仍在悬停提示里 */}
        <span className="tabular-nums edit-short:hidden">{t("edit_view_seconds", { value: formatSeconds(clip.duration) })}</span>
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
            className={cn(
              "absolute top-[calc(var(--lane-top)+4px)] left-(--at) flex h-[calc(var(--lane-height)-8px)] w-[calc(var(--span)-2px)] items-center overflow-hidden text-ellipsis rounded-sm border px-1 text-xs leading-none whitespace-nowrap",
              span.postProduction
                ? "border-border bg-muted text-muted-foreground"
                : span.missingAudio
                  ? "border-dashed border-input text-muted-foreground"
                  : "border-black/30 bg-unit-narration text-white",
            )}
            style={
              {
                "--at": percentOf(span.start, duration),
                "--span": percentOf(span.end - span.start, duration),
                "--unit-hue": unitHue(span.unitId),
                "--lane-top": `${(span.lane / lanes) * 100}%`,
                "--lane-height": `${100 / lanes}%`,
              } as CSSProperties
            }
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
          className="absolute inset-y-1 left-(--at) flex w-[calc(var(--span)-1px)] items-center overflow-hidden text-ellipsis rounded-xs border border-border bg-muted px-1 text-xs leading-none whitespace-nowrap text-subtle-foreground"
          style={
            {
              "--at": percentOf(item.start, duration),
              "--span": percentOf(item.end - item.start, duration),
            } as CSSProperties
          }
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
            className="absolute inset-y-1 left-(--at) flex w-[calc(var(--span)-1px)] items-center overflow-hidden text-ellipsis rounded-xs px-1 text-xs leading-none whitespace-nowrap text-white bgm-fade"
            style={
              {
                "--at": percentOf(item.start, duration),
                "--span": percentOf(length, duration),
                "--fade-in": `${fadeIn}%`,
                "--fade-out": `${fadeOut}%`,
              } as CSSProperties
            }
          >
            {item.name ?? item.sourceId}
          </span>
        );
      })}
    </>
  );
});
