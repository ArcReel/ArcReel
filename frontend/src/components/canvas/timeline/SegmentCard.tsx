import { useState, useRef, useEffect } from "react";
import { ImageIcon, Film, Clock } from "lucide-react";
import { API } from "@/api";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AvatarStack } from "@/components/ui/AvatarStack";
import { ClueStack } from "@/components/ui/ClueStack";
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
import type {
  NarrationSegment,
  DramaScene,
  Character,
  Clue,
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

function getClueNames(segment: Segment, mode: "narration" | "drama"): string[] {
  return getSegmentField(segment, mode, "clues_in_segment", "clues_in_scene");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStructuredImagePromptValue(value: unknown): value is ImagePrompt {
  if (!isRecord(value) || typeof value.scene !== "string") {
    return false;
  }

  const composition = value.composition;
  if (!isRecord(composition)) {
    return false;
  }

  return (
    typeof composition.shot_type === "string" &&
    typeof composition.lighting === "string" &&
    typeof composition.ambiance === "string"
  );
}

function isStructuredVideoPromptValue(value: unknown): value is VideoPrompt {
  if (
    !isRecord(value) ||
    typeof value.action !== "string" ||
    typeof value.camera_motion !== "string" ||
    typeof value.ambiance_audio !== "string"
  ) {
    return false;
  }

  const dialogue = value.dialogue;
  if (dialogue === undefined) {
    return true;
  }
  if (!Array.isArray(dialogue)) {
    return false;
  }

  return dialogue.every(
    (item) =>
      isRecord(item) &&
      typeof item.speaker === "string" &&
      typeof item.line === "string"
  );
}

function mergePromptPatch<T extends Record<string, unknown>>(
  base: T,
  patch: Record<string, unknown>
): T {
  const merged: Record<string, unknown> = { ...base };

  for (const [k, v] of Object.entries(patch)) {
    if (
      isRecord(v) &&
      isRecord(base[k]) &&
      !Array.isArray(v) &&
      !Array.isArray(base[k])
    ) {
      merged[k] = { ...(base[k] as Record<string, unknown>), ...v };
    } else {
      merged[k] = v;
    }
  }

  return merged as T;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SegmentCardProps {
  segment: Segment;
  contentMode: "narration" | "drama";
  aspectRatio: string; // "9:16" or "16:9"
  characters: Record<string, Character>;
  clues: Record<string, Clue>;
  projectName: string;
  onUpdatePrompt?: (
    segmentId: string,
    field: string,
    value: unknown
  ) => void;
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

const DURATION_OPTIONS = [4, 6, 8];

/** Duration selector — clickable when onUpdatePrompt is provided, read-only otherwise. */
function DurationSelector({
  seconds,
  segmentId,
  onUpdatePrompt,
}: {
  seconds: number;
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  if (!onUpdatePrompt) {
    return (
      <span className="inline-flex items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300">
        <Clock className="h-3 w-3" />
        {seconds}s
      </span>
    );
  }

  return (
    <>
      <button
        ref={ref}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex cursor-pointer items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300 hover:bg-gray-600"
      >
        <Clock className="h-3 w-3" />
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
        <div className="flex gap-1">
          {DURATION_OPTIONS.map((d) => (
            <button
              key={d}
              onClick={() => {
                onUpdatePrompt(segmentId, "duration_seconds", d);
                setOpen(false);
              }}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
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
  onUpdateNote?: (value: string) => void;
}) {
  const [noteDraft, setNoteDraft] = useState(segment.note ?? "");
  const committedRef = useRef(segment.note ?? "");

  useEffect(() => {
    setNoteDraft(segment.note ?? "");
    committedRef.current = segment.note ?? "";
  }, [segment.note]);

  const handleNoteBlur = () => {
    if (noteDraft !== committedRef.current) {
      committedRef.current = noteDraft;
      onUpdateNote?.(noteDraft);
    }
  };

  const noteSection = (
    <div className="mt-auto pt-3 border-t border-gray-800">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 block">
        备注
      </span>
      <textarea
        className="w-full resize-none rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-300 placeholder-gray-600 focus:border-indigo-500 focus:outline-none"
        rows={4}
        placeholder="添加备注..."
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
          原文
        </span>
        <pre className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300 font-sans">
          {s.novel_text || "（暂无原文）"}
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
        对话
      </span>
      {dialogue.length === 0 ? (
        <p className="text-sm text-gray-500 italic">（暂无对话）</p>
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
  contentMode,
  segmentId,
  onUpdatePrompt,
}: {
  segment: Segment;
  contentMode: "narration" | "drama";
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
}) {
  const { image_prompt, video_prompt } = segment;

  const isStructuredImage = isStructuredImagePromptValue(image_prompt);
  const isStructuredVideo = isStructuredVideoPromptValue(video_prompt);

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
      setImgDraft(null);
      setImgText(promptToStr(image_prompt, "scene"));
    }
  }, [image_prompt, isStructuredImage]);

  useEffect(() => {
    if (!isStructuredVideo) {
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
      onUpdatePrompt?.(segmentId, "image_prompt", merged);
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
      onUpdatePrompt?.(segmentId, "video_prompt", merged);
      return merged;
    });
  };

  const fireString = (field: string, value: string) => {
    onUpdatePrompt?.(segmentId, field, value);
  };

  return (
    <div className="flex flex-col gap-3 p-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
        提示词
      </span>

      {/* ---- Image Prompt ---- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <ImageIcon className="h-3.5 w-3.5 text-gray-500" />
          <span className="text-[11px] font-semibold text-gray-400">
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
            value={imgText}
            onChange={(v) => {
              setImgText(v);
              fireString("image_prompt", v);
            }}
            placeholder="分镜图描述..."
          />
        )}
      </div>

      {/* ---- Video Prompt ---- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Film className="h-3.5 w-3.5 text-gray-500" />
          <span className="text-[11px] font-semibold text-gray-400">
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
            value={vidText}
            onChange={(v) => {
              setVidText(v);
              fireString("video_prompt", v);
            }}
            placeholder="视频动作描述..."
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
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
}) {
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
  ) as "9:16" | "16:9";

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* ---- Storyboard image (always shown) ---- */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <ImageIcon className="h-3 w-3 text-gray-500" />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">分镜图</span>
          </div>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="storyboards"
            resourceId={segmentId}
            onRestore={onRestoreStoryboard}
          />
        </div>
        <PreviewableImageFrame src={storyboardUrl} alt={`${segmentId} 分镜图`}>
          <AspectFrame ratio={normalizedRatio}>
            <ImageFlipReveal
              src={storyboardUrl}
              alt={`${segmentId} 分镜图`}
              loading="lazy"
              className="h-full w-full object-cover"
              fallback={
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-600">
                  <ImageIcon className="h-8 w-8" />
                  <span className="text-xs">暂无分镜</span>
                </div>
              }
            />
          </AspectFrame>
        </PreviewableImageFrame>
        <div className="mt-2">
          <GenerateButton
            onClick={() => onGenerateStoryboard?.(segmentId)}
            loading={generatingStoryboard}
            label="生成分镜"
            className="w-full justify-center"
          />
        </div>
      </div>

      {/* ---- Video (shown when available or as placeholder) ---- */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Film className="h-3 w-3 text-gray-500" />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">视频</span>
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
              {assets?.storyboard_image ? "可生成视频" : "需先生成分镜"}
            </span>
          </div>
        )}
        <div className="mt-2">
          <GenerateButton
            onClick={() => onGenerateVideo?.(segmentId)}
            loading={generatingVideo}
            label="生成视频"
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
  clues,
  projectName,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard = false,
  generatingVideo = false,
}: SegmentCardProps) {
  const segmentId = getSegmentId(segment, contentMode);
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));
  const charNames = getCharacterNames(segment, contentMode);
  const clueNames = getClueNames(segment, contentMode);

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
            />
            {segCost && (
              <>
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">预估</span>
                <span className="text-[11px] text-gray-500">分镜 <span className="text-gray-400">{formatCost(segCost.estimate.image)}</span></span>
                <span className="text-[11px] text-gray-500">视频 <span className="text-gray-400">{formatCost(segCost.estimate.video)}</span></span>
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">实际</span>
                <span className="text-[11px] text-gray-500">分镜 <span className="text-gray-400">{formatCost(segCost.actual.image)}</span></span>
                <span className="text-[11px] text-gray-500">视频 <span className="text-gray-400">{formatCost(segCost.actual.video)}</span></span>
              </>
            )}
          </div>

          {/* Right: AvatarStack + ClueStack */}
          <div className="flex items-center gap-2">
            <AvatarStack
              names={charNames}
              characters={characters}
              projectName={projectName}
            />
            {charNames.length > 0 && clueNames.length > 0 && (
              <div className="border-l border-gray-700 self-stretch" />
            )}
            <ClueStack
              names={clueNames}
              clues={clues}
              projectName={projectName}
            />
          </div>
        </div>

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
