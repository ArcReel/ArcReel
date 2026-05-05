import { useState, useRef, useEffect, useId } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { ImageIcon, Film, Clock, Edit3 } from "lucide-react";
import { API } from "@/api";
import { DEFAULT_DURATIONS } from "@/utils/provider-models";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AvatarStack } from "@/components/ui/AvatarStack";
import { ClueStack } from "@/components/ui/ClueStack";
import {
  SegmentRefsEditModal,
  type SegmentRefsChanges,
} from "@/components/ui/SegmentRefsEditModal";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { Popover } from "@/components/ui/Popover";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { ImagePromptEditor } from "./ImagePromptEditor";
import { VideoPromptEditor } from "./VideoPromptEditor";
import { formatCost } from "@/utils/cost-format";
import {
  isStructuredImagePrompt,
  isStructuredVideoPrompt,
  mergePromptPatch,
} from "@/utils/prompt-shape";
import type {
  NarrationSegment,
  DramaScene,
  Character,
  Scene,
  Prop,
  ImagePrompt,
  VideoPrompt,
  TransitionType,
} from "@/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TRANSITION_LABELS: Record<TransitionType, string> = {
  cut: "Cut",
  fade: "Fade",
  dissolve: "Dissolve",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Segment = NarrationSegment | DramaScene;
type SegmentRefField =
  | "characters_in_segment"
  | "characters_in_scene"
  | "scenes"
  | "props";

function getSegmentId(segment: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (segment as NarrationSegment).segment_id
    : (segment as DramaScene).scene_id;
}

function getSegmentField(
  segment: Segment,
  mode: "narration" | "drama",
  narrationKey: keyof NarrationSegment,
  dramaKey: keyof DramaScene,
): string[] {
  return mode === "narration"
    ? (((segment as NarrationSegment)[narrationKey] as string[] | undefined) ?? [])
    : (((segment as DramaScene)[dramaKey] as string[] | undefined) ?? []);
}

function getCharacterNames(segment: Segment, mode: "narration" | "drama"): string[] {
  return getSegmentField(segment, mode, "characters_in_segment", "characters_in_scene");
}

function getSceneNames(segment: Segment, _mode: "narration" | "drama"): string[] {
  return segment.scenes ?? [];
}

function getPropNames(segment: Segment, _mode: "narration" | "drama"): string[] {
  return segment.props ?? [];
}


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SegmentCardProps {
  segment: Segment;
  contentMode: "narration" | "drama";
  aspectRatio: string; // "9:16" or "16:9"
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
  projectName: string;
  durationOptions?: number[];
  /** When true, hides the per-scene "生成分镜" button (grid mode manages storyboards). */
  isGridMode?: boolean;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
  ) => void | Promise<void>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Duration selector — clickable when onUpdatePrompt is provided, read-only otherwise. */
function DurationSelector({
  seconds,
  segmentId,
  onUpdatePrompt,
  durationOptions = DEFAULT_DURATIONS as number[],
}: {
  seconds: number;
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void | Promise<void>;
  durationOptions?: number[];
}) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  if (!onUpdatePrompt) {
    return (
      <span className="inline-flex items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300">
        <Clock aria-hidden="true" className="h-3 w-3" />
        {seconds}s
      </span>
    );
  }

  return (
    <>
      <button
        ref={ref}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex cursor-pointer items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300 hover:bg-gray-600 focus-ring"
      >
        <Clock aria-hidden="true" className="h-3 w-3" />
        {seconds}s
      </button>
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={ref}
        width="w-auto"
        className="rounded-lg border border-gray-700 p-1.5 shadow-xl"
        align="start"
        sideOffset={6}
      >
        <div className="flex gap-1" role="radiogroup" aria-label={t("duration_selector_aria")}>
          {durationOptions.map((d) => (
            <button
              key={d}
              role="radio"
              aria-checked={d === seconds}
              onClick={() => {
                void onUpdatePrompt(segmentId, "duration_seconds", d);
                setOpen(false);
              }}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors focus-ring ${
                d === seconds
                  ? "bg-indigo-600 text-white"
                  : "text-gray-300 hover:bg-gray-700"
              }`}
            >
              {d}s
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}

/** Segment break separator rendered above a card when segment_break is true. */
function SegmentBreakSeparator() {
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="flex-1 border-t-2 border-dashed border-amber-600/40" />
      <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-500/70">
        Segment Break
      </span>
      <div className="flex-1 border-t-2 border-dashed border-amber-600/40" />
    </div>
  );
}

/** Transition indicator between cards. */
function TransitionIndicator({ type }: { type: TransitionType }) {
  return (
    <div className="flex items-center justify-center py-1.5">
      <span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] font-medium text-gray-500">
        {TRANSITION_LABELS[type] ?? type}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column 1 — Text area
// ---------------------------------------------------------------------------

function TextColumn({
  segment,
  contentMode,
  onUpdateNote,
}: {
  segment: Segment;
  contentMode: "narration" | "drama";
  onUpdateNote?: (value: string) => void | Promise<void>;
}) {
  const { t } = useTranslation("dashboard");
  const [noteDraft, setNoteDraft] = useState(segment.note ?? "");
  const committedRef = useRef(segment.note ?? "");
  const noteId = useId();

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 可编辑备注字段随外部数据更新同步，拷贝模式是有意设计
    setNoteDraft(segment.note ?? "");
    committedRef.current = segment.note ?? "";
  }, [segment.note]);

  const handleNoteBlur = () => {
    if (noteDraft !== committedRef.current) {
      committedRef.current = noteDraft;
      void onUpdateNote?.(noteDraft);
    }
  };

  const noteSection = (
    <div className="mt-auto pt-3 border-t border-gray-800">
      <label htmlFor={noteId} className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 block">
        {t("note_section")}
      </label>
      <textarea
        id={noteId}
        className="w-full resize-none rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-300 placeholder-gray-600 focus:border-indigo-500 focus-ring"
        rows={4}
        placeholder={t("add_note_placeholder")}
        value={noteDraft}
        onChange={(e) => setNoteDraft(e.target.value)}
        onBlur={handleNoteBlur}
      />
    </div>
  );

  if (contentMode === "narration") {
    const s = segment as NarrationSegment;
    return (
      <div className="flex h-full flex-col gap-1.5 p-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
          {t("original_text")}
        </span>
        <pre className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300 font-sans">
          {s.novel_text || t("no_original_text")}
        </pre>
        {noteSection}
      </div>
    );
  }

  // Drama mode — show dialogue list
  const s = segment as DramaScene;
  const vp = s.video_prompt;
  const dialogue = (typeof vp === "object" && vp !== null && "dialogue" in vp)
    ? (vp.dialogue ?? [])
    : [];
  return (
    <div className="flex h-full flex-col gap-1.5 p-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
        {t("dialogue_section")}
      </span>
      {dialogue.length === 0 ? (
        <p className="text-sm text-gray-500 italic">{t("no_dialogue")}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {dialogue.map((d: { speaker: string; line: string }, i: number) => (
            <li key={i} className="text-sm text-gray-300">
              <span className="font-bold text-indigo-400">{d.speaker}</span>
              <span className="mx-1 text-gray-600">:</span>
              <span>{d.line}</span>
            </li>
          ))}
        </ul>
      )}
      {noteSection}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column 2 — Prompt area
// ---------------------------------------------------------------------------

function PromptColumn({
  segment,
  contentMode: _contentMode,
  segmentId,
  onUpdatePrompt,
}: {
  segment: Segment;
  contentMode: "narration" | "drama";
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void | Promise<void>;
}) {
  const { t } = useTranslation("dashboard");
  const { image_prompt, video_prompt } = segment;
  const imgLabelId = useId();
  const vidLabelId = useId();

  const isStructuredImage = isStructuredImagePrompt(image_prompt);
  const isStructuredVideo = isStructuredVideoPrompt(video_prompt);

  // ---- String fallback state (only used when prompts are plain strings) ----
  const promptToStr = (p: unknown, key: string): string => {
    if (typeof p === "string") return p;
    if (typeof p === "object" && p !== null) {
      const val = (p as Record<string, unknown>)[key];
      if (typeof val === "string") return val;
    }
    return "";
  };

  const [imgText, setImgText] = useState(() => promptToStr(image_prompt, "scene"));
  const [vidText, setVidText] = useState(() => promptToStr(video_prompt, "action"));
  const [imgDraft, setImgDraft] = useState<ImagePrompt | null>(() =>
    isStructuredImage ? image_prompt : null
  );
  const [vidDraft, setVidDraft] = useState<VideoPrompt | null>(() =>
    isStructuredVideo ? video_prompt : null
  );
  const prevSegmentIdRef = useRef(segmentId);

  useEffect(() => {
    if (prevSegmentIdRef.current === segmentId) {
      return;
    }

    prevSegmentIdRef.current = segmentId;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 切换镜头时重置本地草稿，是导航驱动的有意状态重置
    setImgText(promptToStr(image_prompt, "scene"));
    setVidText(promptToStr(video_prompt, "action"));
    setImgDraft(isStructuredImage ? image_prompt : null);
    setVidDraft(isStructuredVideo ? video_prompt : null);
  }, [
    segmentId,
    image_prompt,
    video_prompt,
    isStructuredImage,
    isStructuredVideo,
  ]);

  useEffect(() => {
    if (!isStructuredImage) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- image_prompt 结构切换时同步本地编辑态，拷贝模式是有意设计
      setImgDraft(null);
      setImgText(promptToStr(image_prompt, "scene"));
    }
  }, [image_prompt, isStructuredImage]);

  useEffect(() => {
    if (!isStructuredVideo) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- video_prompt 结构切换时同步本地编辑态，拷贝模式是有意设计
      setVidDraft(null);
      setVidText(promptToStr(video_prompt, "action"));
    }
  }, [video_prompt, isStructuredVideo]);

  // ---- Firing helpers ----
  const fireStructuredImage = (patch: Partial<ImagePrompt>) => {
    setImgDraft((prev) => {
      const base = prev ?? (isStructuredImage ? image_prompt : null);
      if (!base) {
        return prev;
      }
      const merged = mergePromptPatch(
        base as unknown as Record<string, unknown>,
        patch as Record<string, unknown>
      ) as unknown as ImagePrompt;
      void onUpdatePrompt?.(segmentId, "image_prompt", merged);
      return merged;
    });
  };

  const fireStructuredVideo = (patch: Partial<VideoPrompt>) => {
    setVidDraft((prev) => {
      const base = prev ?? (isStructuredVideo ? video_prompt : null);
      if (!base) {
        return prev;
      }
      const merged = mergePromptPatch(
        base as unknown as Record<string, unknown>,
        patch as Record<string, unknown>
      ) as unknown as VideoPrompt;
      void onUpdatePrompt?.(segmentId, "video_prompt", merged);
      return merged;
    });
  };

  const fireString = (field: string, value: string) => {
    void onUpdatePrompt?.(segmentId, field, value);
  };

  return (
    <div className="flex flex-col gap-3 p-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
        {t("prompt_section")}
      </span>

      {/* ---- Image Prompt ---- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <ImageIcon className="h-3.5 w-3.5 text-gray-500" />
          <span id={imgLabelId} className="text-[11px] font-semibold text-gray-400">
            Image Prompt
          </span>
        </div>

        {isStructuredImage && imgDraft ? (
          <ImagePromptEditor
            prompt={imgDraft}
            onUpdate={fireStructuredImage}
          />
        ) : (
          <AutoTextarea
            aria-labelledby={imgLabelId}
            value={imgText}
            onChange={(v) => {
              setImgText(v);
              fireString("image_prompt", v);
            }}
            placeholder={t("storyboard_prompt_placeholder")}
          />
        )}
      </div>

      {/* ---- Video Prompt ---- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Film className="h-3.5 w-3.5 text-gray-500" />
          <span id={vidLabelId} className="text-[11px] font-semibold text-gray-400">
            Video Prompt
          </span>
        </div>

        {isStructuredVideo && vidDraft ? (
          <VideoPromptEditor
            prompt={vidDraft}
            onUpdate={fireStructuredVideo}
          />
        ) : (
          <AutoTextarea
            aria-labelledby={vidLabelId}
            value={vidText}
            onChange={(v) => {
              setVidText(v);
              fireString("video_prompt", v);
            }}
            placeholder={t("video_prompt_placeholder")}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column 3 — Visual media area
// ---------------------------------------------------------------------------

/** Simple video player with poster thumbnail and lazy preload. */
function VideoPlayer({ src, poster }: { src: string; poster?: string | null }) {
  return (
    // eslint-disable-next-line jsx-a11y/media-has-caption -- 生成式预览视频暂无字幕源，将来如引入字幕生成则移除此 disable
    <video
      src={src}
      poster={poster ?? undefined}
      className="h-full w-full bg-black object-contain"
      controls
      playsInline
      preload={poster ? "none" : "metadata"}
    />
  );
}

function MediaColumn({
  segment,
  aspectRatio,
  projectName,
  segmentId,
  isGridMode,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
}: {
  segment: Segment;
  aspectRatio: string;
  projectName: string;
  segmentId: string;
  isGridMode?: boolean;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const assets = segment.generated_assets;
  const storyboardFp = useProjectsStore(
    (s) => assets?.storyboard_image ? s.getAssetFingerprint(assets.storyboard_image) : null,
  );
  const videoFp = useProjectsStore(
    (s) => assets?.video_clip ? s.getAssetFingerprint(assets.video_clip) : null,
  );
  const thumbnailFp = useProjectsStore(
    (s) => assets?.video_thumbnail ? s.getAssetFingerprint(assets.video_thumbnail) : null,
  );
  const storyboardUrl = assets?.storyboard_image
    ? API.getFileUrl(projectName, assets.storyboard_image, storyboardFp)
    : null;
  const videoUrl = assets?.video_clip
    ? API.getFileUrl(projectName, assets.video_clip, videoFp)
    : null;
  const thumbnailUrl = assets?.video_thumbnail
    ? API.getFileUrl(projectName, assets.video_thumbnail, thumbnailFp)
    : null;

  // Normalize aspect ratio to the union type expected by AspectFrame
  const normalizedRatio = (
    aspectRatio === "9:16" || aspectRatio === "16:9" ? aspectRatio : "16:9"
  );

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* ---- Storyboard image (always shown) ---- */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <ImageIcon className="h-3 w-3 text-gray-500" />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">{t("storyboard_section")}</span>
          </div>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="storyboards"
            resourceId={segmentId}
            onRestore={onRestoreStoryboard}
          />
        </div>

        <PreviewableImageFrame src={storyboardUrl} alt={`${segmentId} ${t("storyboard_section")}`}>
          <AspectFrame ratio={normalizedRatio}>
            <ImageFlipReveal
              src={storyboardUrl}
              alt={`${segmentId} ${t("storyboard_section")}`}
              loading="lazy"
              className="h-full w-full object-cover"
              fallback={
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-600">
                  <ImageIcon className="h-8 w-8" />
                  <span className="text-xs">{t("no_storyboard")}</span>
                </div>
              }
            />
          </AspectFrame>
        </PreviewableImageFrame>

        {!isGridMode && (
          <div className="mt-2">
            <GenerateButton
              onClick={() => onGenerateStoryboard?.(segmentId)}
              loading={generatingStoryboard}
              label={t("generate_storyboard")}
              className="w-full justify-center"
            />
          </div>
        )}
      </div>

      {/* ---- Video (shown when available or as placeholder) ---- */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Film className="h-3 w-3 text-gray-500" />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">{t("video_section")}</span>
          </div>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="videos"
            resourceId={segmentId}
            onRestore={onRestoreVideo}
          />
        </div>
        {videoUrl ? (
          <AspectFrame ratio={normalizedRatio}>
            <VideoPlayer src={videoUrl} poster={thumbnailUrl} />
          </AspectFrame>
        ) : (
          <div className="flex items-center justify-center rounded-lg border border-dashed border-gray-700 bg-gray-800/30 py-4">
            <span className="text-xs text-gray-600">
              {assets?.storyboard_image ? t("can_generate_video") : t("need_storyboard_first")}
            </span>
          </div>
        )}
        <div className="mt-2">
          <GenerateButton
            onClick={() => onGenerateVideo?.(segmentId)}
            loading={generatingVideo}
            label={t("generate_video_btn")}
            className="w-full justify-center"
            disabled={!assets?.storyboard_image}
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SegmentCard (main export)
// ---------------------------------------------------------------------------

export function SegmentCard({
  segment,
  contentMode,
  aspectRatio,
  characters,
  scenes,
  props,
  projectName,
  durationOptions,
  isGridMode,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard = false,
  generatingVideo = false,
}: SegmentCardProps) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const segmentId = getSegmentId(segment, contentMode);
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));
  const charNames = getCharacterNames(segment, contentMode);
  const sceneNames = getSceneNames(segment, contentMode);
  const propNames = getPropNames(segment, contentMode);
  const charField: SegmentRefField =
    contentMode === "drama" ? "characters_in_scene" : "characters_in_segment";
  const editable = !!onUpdatePrompt;
  const [refsModalOpen, setRefsModalOpen] = useState(false);

  const handleSaveRefs = (changes: SegmentRefsChanges) => {
    if (!onUpdatePrompt) return;
    const patch: Record<string, unknown> = {};
    if (changes.characters !== undefined) patch[charField] = changes.characters;
    if (changes.scenes !== undefined) patch.scenes = changes.scenes;
    if (changes.props !== undefined) patch.props = changes.props;
    if (Object.keys(patch).length > 0) {
      void onUpdatePrompt(segmentId, patch);
    }
    setRefsModalOpen(false);
  };

  return (
    <div>
      {/* Segment break separator */}
      {segment.segment_break && <SegmentBreakSeparator />}

      {/* Main card */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {/* ---- Header ---- */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800">
          {/* Left: ID badge + duration */}
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs bg-gray-800 rounded px-1.5 py-0.5 text-gray-300">
              {segmentId}
            </span>
            <DurationSelector
              seconds={segment.duration_seconds}
              segmentId={segmentId}
              onUpdatePrompt={onUpdatePrompt}
              durationOptions={durationOptions}
            />
            {segCost && (
              <span className="tabular-nums contents">
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">{t("cost_estimate_short")}</span>
                <span className="text-[11px] text-gray-500">{t("cost_storyboard_short")} <span className="text-gray-400">{formatCost(segCost.estimate.image)}</span></span>
                <span className="text-[11px] text-gray-500">{t("cost_video_short")} <span className="text-gray-400">{formatCost(segCost.estimate.video)}</span></span>
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">{t("cost_actual_short")}</span>
                <span className="text-[11px] text-gray-500">{t("cost_storyboard_short")} <span className="text-gray-400">{formatCost(segCost.actual.image)}</span></span>
                <span className="text-[11px] text-gray-500">{t("cost_video_short")} <span className="text-gray-400">{formatCost(segCost.actual.video)}</span></span>
              </span>
            )}
          </div>

          {/* Right: 只读引用缩略 + 编辑入口（弹模态框批量编辑） */}
          <div className="flex items-center gap-2">
            <div className="flex items-center -space-x-1">
              <AvatarStack
                names={charNames}
                characters={characters}
                projectName={projectName}
              />
              <ClueStack
                sceneNames={sceneNames}
                propNames={propNames}
                scenes={scenes}
                props={props}
                projectName={projectName}
              />
            </div>
            {editable && (
              <button
                type="button"
                onClick={() => setRefsModalOpen(true)}
                aria-label={t("segment_refs_edit_button")}
                title={t("segment_refs_edit_button")}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-transparent text-gray-500 transition-colors hover:border-gray-700 hover:bg-gray-800 hover:text-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40"
              >
                <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        {editable && refsModalOpen && (
          <SegmentRefsEditModal
            open
            onClose={() => setRefsModalOpen(false)}
            onSave={handleSaveRefs}
            initialCharacters={charNames}
            initialScenes={sceneNames}
            initialProps={propNames}
            characters={characters}
            scenes={scenes}
            props={props}
            projectName={projectName}
            onManageClick={(kind) => setLocation(`/${kind}s`)}
          />
        )}

        {/* ---- Content: three-column grid ---- */}
        <div className="grid grid-cols-3 gap-0 divide-x divide-gray-800">
          {/* Column 1 — Text */}
          <TextColumn
            segment={segment}
            contentMode={contentMode}
            onUpdateNote={(value) => onUpdatePrompt?.(segmentId, "note", value)}
          />

          {/* Column 2 — Prompts */}
          <PromptColumn
            segment={segment}
            contentMode={contentMode}
            segmentId={segmentId}
            onUpdatePrompt={onUpdatePrompt}
          />

          {/* Column 3 — Media */}
          <MediaColumn
            segment={segment}
            aspectRatio={aspectRatio}
            projectName={projectName}
            segmentId={segmentId}
            isGridMode={isGridMode}
            onGenerateStoryboard={onGenerateStoryboard}
            onGenerateVideo={onGenerateVideo}
            onRestoreStoryboard={onRestoreStoryboard}
            onRestoreVideo={onRestoreVideo}
            generatingStoryboard={generatingStoryboard}
            generatingVideo={generatingVideo}
          />
        </div>
      </div>

      {/* Transition indicator to next card */}
      <TransitionIndicator type={segment.transition_to_next} />
    </div>
  );
}
