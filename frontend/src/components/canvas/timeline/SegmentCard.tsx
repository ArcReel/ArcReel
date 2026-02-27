import { useState, useRef, useEffect, useCallback } from "react";
import { ImageIcon, Film, Clock, Play, Pause } from "lucide-react";
import { API } from "@/api";
import { AvatarStack } from "@/components/ui/AvatarStack";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import type {
  NarrationSegment,
  DramaScene,
  Character,
  Clue,
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

function getCharacterNames(
  segment: Segment,
  mode: "narration" | "drama"
): string[] {
  return mode === "narration"
    ? ((segment as NarrationSegment).characters_in_segment ?? [])
    : ((segment as DramaScene).characters_in_scene ?? []);
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
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Duration badge (4s / 6s / 8s). */
function DurationBadge({ seconds }: { seconds: number }) {
  return (
    <span className="inline-flex items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300">
      <Clock className="h-3 w-3" />
      {seconds}s
    </span>
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
}: {
  segment: Segment;
  contentMode: "narration" | "drama";
}) {
  if (contentMode === "narration") {
    const s = segment as NarrationSegment;
    return (
      <div className="flex flex-col gap-1.5 p-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
          原文
        </span>
        <pre className="whitespace-pre-wrap text-sm leading-relaxed text-gray-300 font-sans">
          {s.novel_text || "（暂无原文）"}
        </pre>
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
    <div className="flex flex-col gap-1.5 p-3">
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column 2 — Prompt area
// ---------------------------------------------------------------------------

/** Auto-resizing textarea. */
function AutoTextarea({
  value,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  const resize = useCallback(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onInput={resize}
      placeholder={placeholder}
      rows={2}
      className={`w-full resize-none overflow-hidden bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none ${className ?? ""}`}
    />
  );
}

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

  // Prompts can be plain strings or structured objects depending on the project.
  const promptToStr = (p: unknown, key: string): string => {
    if (typeof p === "string") return p;
    if (typeof p === "object" && p !== null) {
      const val = (p as Record<string, unknown>)[key];
      if (typeof val === "string") return val;
      return JSON.stringify(p);
    }
    return "";
  };

  const [imgText, setImgText] = useState(() => promptToStr(image_prompt, "scene"));
  const [vidText, setVidText] = useState(() => promptToStr(video_prompt, "action"));

  // Sync from props
  useEffect(() => {
    setImgText(promptToStr(image_prompt, "scene"));
    setVidText(promptToStr(video_prompt, "action"));
  }, [image_prompt, video_prompt]);

  // When the original prompt is a structured object, patch only the text field
  // (scene for image_prompt, action for video_prompt) so the backend receives
  // a valid dict. When it's a plain string, send the string as-is.
  const buildPromptValue = (field: string, text: string): unknown => {
    const original = field === "image_prompt" ? image_prompt : video_prompt;
    if (typeof original === "object" && original !== null) {
      const key = field === "image_prompt" ? "scene" : "action";
      return { ...(original as unknown as Record<string, unknown>), [key]: text };
    }
    return text;
  };

  const fire = (field: string, value: string) => {
    onUpdatePrompt?.(segmentId, field, buildPromptValue(field, value));
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

        <AutoTextarea
          value={imgText}
          onChange={(v) => {
            setImgText(v);
            fire("image_prompt", v);
          }}
          placeholder="分镜图描述..."
        />
      </div>

      {/* ---- Video Prompt ---- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Film className="h-3.5 w-3.5 text-gray-500" />
          <span className="text-[11px] font-semibold text-gray-400">
            Video Prompt
          </span>
        </div>

        <AutoTextarea
          value={vidText}
          onChange={(v) => {
            setVidText(v);
            fire("video_prompt", v);
          }}
          placeholder="视频动作描述..."
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column 3 — Visual media area
// ---------------------------------------------------------------------------

/** Simple video player with play/pause toggle. */
function VideoPlayer({ src }: { src: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);

  const toggle = () => {
    const el = videoRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      el.play();
    }
    setPlaying(!playing);
  };

  return (
    <div className="relative h-full w-full">
      <video
        ref={videoRef}
        src={src}
        className="h-full w-full object-cover"
        loop
        muted
        playsInline
        onEnded={() => setPlaying(false)}
      />
      <button
        type="button"
        onClick={toggle}
        className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 transition-opacity hover:opacity-100"
      >
        {playing ? (
          <Pause className="h-8 w-8 text-white drop-shadow-lg" />
        ) : (
          <Play className="h-8 w-8 text-white drop-shadow-lg" />
        )}
      </button>
    </div>
  );
}

function MediaColumn({
  segment,
  aspectRatio,
  projectName,
  segmentId,
  onGenerateStoryboard,
  onGenerateVideo,
  generatingStoryboard,
  generatingVideo,
}: {
  segment: Segment;
  aspectRatio: string;
  projectName: string;
  segmentId: string;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
}) {
  const assets = segment.generated_assets;
  const storyboardUrl = assets?.storyboard_image
    ? API.getFileUrl(projectName, assets.storyboard_image)
    : null;
  const videoUrl = assets?.video_clip
    ? API.getFileUrl(projectName, assets.video_clip)
    : null;

  // Normalize aspect ratio to the union type expected by AspectFrame
  const normalizedRatio = (
    aspectRatio === "9:16" || aspectRatio === "16:9" ? aspectRatio : "16:9"
  ) as "9:16" | "16:9";

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* ---- Storyboard image (always shown) ---- */}
      <div>
        <div className="flex items-center gap-1.5 mb-1.5">
          <ImageIcon className="h-3 w-3 text-gray-500" />
          <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">分镜图</span>
        </div>
        <AspectFrame ratio={normalizedRatio}>
          <ImageFlipReveal
            src={storyboardUrl}
            alt={`${segmentId} storyboard`}
            className="h-full w-full object-cover"
            fallback={
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-600">
                <ImageIcon className="h-8 w-8" />
                <span className="text-xs">暂无分镜</span>
              </div>
            }
          />
        </AspectFrame>
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
        <div className="flex items-center gap-1.5 mb-1.5">
          <Film className="h-3 w-3 text-gray-500" />
          <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">视频</span>
        </div>
        {videoUrl ? (
          <AspectFrame ratio={normalizedRatio}>
            <VideoPlayer src={videoUrl} />
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
  clues: _clues,
  projectName,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  generatingStoryboard = false,
  generatingVideo = false,
}: SegmentCardProps) {
  const segmentId = getSegmentId(segment, contentMode);
  const charNames = getCharacterNames(segment, contentMode);

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
            <DurationBadge seconds={segment.duration_seconds} />
          </div>

          {/* Right: AvatarStack */}
          <AvatarStack
            names={charNames}
            characters={characters}
            projectName={projectName}
          />
        </div>

        {/* ---- Content: three-column grid ---- */}
        <div className="grid grid-cols-3 gap-0 divide-x divide-gray-800">
          {/* Column 1 — Text */}
          <TextColumn segment={segment} contentMode={contentMode} />

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
