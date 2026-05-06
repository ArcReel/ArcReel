import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ImageIcon,
  Film,
  ChevronLeft,
  ChevronRight,
  Check,
  Loader2,
  Undo2,
} from "lucide-react";
import type {
  NarrationSegment,
  DramaScene,
  ImagePrompt,
  VideoPrompt,
  Dialogue,
} from "@/types";
import { ImagePromptEditor } from "./ImagePromptEditor";
import { VideoPromptEditor } from "./VideoPromptEditor";
import { DialogueListEditor } from "./DialogueListEditor";
import { ResponsiveDetailGrid } from "./ResponsiveDetailGrid";
import { MediaCard } from "./MediaCard";
import { NotesDrawer } from "./NotesDrawer";
import { StatusBadge, statusFromAssets } from "./StatusBadge";
import { useCostStore } from "@/stores/cost-store";
import {
  isStructuredImagePrompt,
  isStructuredVideoPrompt,
} from "@/utils/prompt-shape";

type Segment = NarrationSegment | DramaScene;
type ImagePromptValue = ImagePrompt | string;
type VideoPromptValue = VideoPrompt | string;

interface ShotDetailProps {
  segment: Segment;
  segmentId: string;
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  isGridMode?: boolean;
  /** Total shot count for "1/N" indicator */
  selectedIndex: number;
  totalCount: number;
  onPrev: () => void;
  onNext: () => void;
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

function getNovelText(seg: Segment, mode: "narration" | "drama"): string {
  if (mode === "narration") return (seg as NarrationSegment).novel_text || "";
  return "";
}

function getSceneType(seg: Segment, mode: "narration" | "drama"): string {
  if (mode === "drama") return (seg as DramaScene).scene_type || "";
  return "";
}

interface DraftState {
  image_prompt: ImagePromptValue;
  video_prompt: VideoPromptValue;
}

// 字段集合稳定（ImagePrompt/VideoPrompt/string），JSON.stringify 即可作等值签名：
// 任何字段顺序差异都来自我们自己的 setter 或上游同一构造路径，键序一致。
const stableSig = (value: unknown): string => JSON.stringify(value ?? null);

export function ShotDetail({
  segment,
  segmentId,
  contentMode,
  aspectRatio,
  projectName,
  isGridMode,
  selectedIndex,
  totalCount,
  onPrev,
  onNext,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
}: ShotDetailProps) {
  const { t } = useTranslation("dashboard");
  const status = statusFromAssets(segment.generated_assets?.status);
  const sceneType = getSceneType(segment, contentMode);
  const novelText = getNovelText(segment, contentMode);
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));

  const ip = segment.image_prompt;
  const vp = segment.video_prompt;
  const note = segment.note ?? "";

  // 草稿：本地编辑直到用户点击 Save。父级 ShotSplitView 通过 key={segmentId}
  // 在切镜头时硬重置整个组件，所以这里只需处理"上游同字段静默更新"的情况。
  // 备注不进入草稿，由 NotesDrawer 收起时直接落库。
  const [draft, setDraft] = useState<DraftState>(() => ({
    image_prompt: ip,
    video_prompt: vp,
  }));
  const [saving, setSaving] = useState(false);

  const upstreamSig = useMemo(
    () => stableSig({ ip, vp }),
    [ip, vp],
  );
  const baselineSigRef = useRef(upstreamSig);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // 上游变更（保存完成 / agent 编辑）：草稿干净时静默跟随；脏时保留用户输入。
  // 把 draft 放到 ref 里读，避免每次 keystroke 都重跑 effect+stringify。
  useEffect(() => {
    if (baselineSigRef.current === upstreamSig) return;
    if (stableSig(draftRef.current) === baselineSigRef.current) {
      setDraft({ image_prompt: ip, video_prompt: vp });
    }
    baselineSigRef.current = upstreamSig;
  }, [upstreamSig, ip, vp]);

  // 引用相等优先：未编辑过的字段直接跳过 stringify。
  const dirtyPatch = useMemo<Record<string, unknown>>(() => {
    const patch: Record<string, unknown> = {};
    if (
      draft.image_prompt !== ip &&
      stableSig(draft.image_prompt) !== stableSig(ip)
    )
      patch.image_prompt = draft.image_prompt;
    if (
      draft.video_prompt !== vp &&
      stableSig(draft.video_prompt) !== stableSig(vp)
    )
      patch.video_prompt = draft.video_prompt;
    return patch;
  }, [draft, ip, vp]);

  const dirty = Object.keys(dirtyPatch).length > 0;


  const isStructIp = isStructuredImagePrompt(draft.image_prompt);
  const isStructVp = isStructuredVideoPrompt(draft.video_prompt);
  const imgDraft: ImagePrompt | null = isStructIp
    ? (draft.image_prompt as ImagePrompt)
    : null;
  const vidDraft: VideoPrompt | null = isStructVp
    ? (draft.video_prompt as VideoPrompt)
    : null;

  const handleImgUpdate = (patch: Partial<ImagePrompt>) => {
    setDraft((d) => {
      if (!isStructuredImagePrompt(d.image_prompt)) return d;
      const merged: ImagePrompt = {
        ...d.image_prompt,
        ...patch,
        composition: {
          ...d.image_prompt.composition,
          ...(patch.composition ?? {}),
        },
      };
      return { ...d, image_prompt: merged };
    });
  };

  const handleVidUpdate = (patch: Partial<VideoPrompt>) => {
    setDraft((d) => {
      if (!isStructuredVideoPrompt(d.video_prompt)) return d;
      const merged: VideoPrompt = { ...d.video_prompt, ...patch };
      return { ...d, video_prompt: merged };
    });
  };

  const handleDialogueChange = (dialogue: Dialogue[]) => {
    handleVidUpdate({ dialogue });
  };

  const handleImgStringChange = (val: string) => {
    setDraft((d) => ({ ...d, image_prompt: val }));
  };

  const handleVidStringChange = (val: string) => {
    setDraft((d) => ({ ...d, video_prompt: val }));
  };

  const handleNotesCommit = (value: string) => {
    if (value === note) return;
    void onUpdatePrompt?.(segmentId, "note", value);
  };

  const handleSave = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await onUpdatePrompt?.(segmentId, dirtyPatch);
      // 上游会刷新 → useEffect 检测到 baselineSig 变化 → 草稿等于新基线时保持干净
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (saving) return;
    setDraft({ image_prompt: ip, video_prompt: vp });
  };

  const sbEstimate = useMemo(
    () =>
      segCost?.estimate?.image
        ? Object.values(segCost.estimate.image).reduce((a, b) => a + b, 0)
        : undefined,
    [segCost?.estimate?.image],
  );
  const vidEstimate = useMemo(
    () =>
      segCost?.estimate?.video
        ? Object.values(segCost.estimate.video).reduce((a, b) => a + b, 0)
        : undefined,
    [segCost?.estimate?.video],
  );

  const assets = segment.generated_assets;
  const hasStoryboard = !!assets?.storyboard_image;

  const dirtyHint = t("shot_detail_save_first");

  const leftColumn = (
    <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-3.5 pb-5 pt-3.5">
      <div>
        <div
          className="mb-2 text-[10.5px] font-bold uppercase"
          style={{
            color: "var(--color-text-4)",
            letterSpacing: "1px",
            fontFamily: "var(--font-mono)",
          }}
        >
          {t("detail_section_dialogue")}
        </div>
        {vidDraft ? (
          <DialogueListEditor
            dialogue={vidDraft.dialogue ?? []}
            onChange={handleDialogueChange}
          />
        ) : (
          <div
            className="rounded-md py-3 text-center text-[11.5px] italic"
            style={{
              border: "1px dashed var(--color-hairline)",
              color: "var(--color-text-4)",
            }}
          >
            {t("detail_dialogue_empty")}
          </div>
        )}
      </div>

      {(novelText || contentMode === "narration") && (
        <div>
          <div
            className="mb-2 text-[10.5px] font-bold uppercase"
            style={{
              color: "var(--color-text-4)",
              letterSpacing: "1px",
              fontFamily: "var(--font-mono)",
            }}
          >
            {t("detail_section_novel")}
          </div>
          <div
            className="rounded-md px-3 py-2.5"
            style={{
              background:
                "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.5), oklch(0.20 0.012 265 / 0.35))",
              border: "1px solid var(--color-hairline-soft)",
              borderLeft: "3px solid var(--color-accent-soft)",
            }}
          >
            <p
              className="display-serif m-0 text-[13px]"
              style={{ lineHeight: 1.65, color: "var(--color-text)" }}
            >
              {novelText || t("detail_dialogue_empty")}
            </p>
          </div>
        </div>
      )}
    </div>
  );

  const midColumn = (
    <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-5 pb-7 pt-3.5">
      <div
        className="text-[10.5px] font-bold uppercase"
        style={{
          color: "var(--color-text-4)",
          letterSpacing: "1px",
          fontFamily: "var(--font-mono)",
        }}
      >
        {t("detail_section_prompts")}
      </div>

      <section>
        <div className="mb-2 flex items-center gap-1.5">
          <ImageIcon
            className="h-3.5 w-3.5"
            style={{ color: "var(--color-text-3)" }}
          />
          <span
            className="text-[12.5px] font-semibold"
            style={{ color: "var(--color-text-2)" }}
          >
            {t("detail_image_prompt_title")}
          </span>
          <span className="flex-1" />
          {imgDraft && (
            <span
              className="num text-[10px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("detail_field_chars_count", { count: imgDraft.scene.length })}
            </span>
          )}
        </div>
        {imgDraft ? (
          <ImagePromptEditor prompt={imgDraft} onUpdate={handleImgUpdate} />
        ) : (
          <textarea
            className="prompt-ta"
            value={
              typeof draft.image_prompt === "string" ? draft.image_prompt : ""
            }
            onChange={(e) => handleImgStringChange(e.target.value)}
            placeholder={t("detail_image_prompt_placeholder")}
            style={{ minHeight: 124 }}
          />
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center gap-1.5">
          <Film
            className="h-3.5 w-3.5"
            style={{ color: "var(--color-text-3)" }}
          />
          <span
            className="text-[12.5px] font-semibold"
            style={{ color: "var(--color-text-2)" }}
          >
            {t("detail_video_prompt_title")}
          </span>
          <span className="flex-1" />
          {vidDraft && (
            <span
              className="num text-[10px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("detail_field_chars_count", { count: vidDraft.action.length })}
            </span>
          )}
        </div>
        {vidDraft ? (
          <VideoPromptEditor prompt={vidDraft} onUpdate={handleVidUpdate} />
        ) : (
          <textarea
            className="prompt-ta"
            value={
              typeof draft.video_prompt === "string" ? draft.video_prompt : ""
            }
            onChange={(e) => handleVidStringChange(e.target.value)}
            placeholder={t("detail_video_prompt_placeholder")}
            style={{ minHeight: 88 }}
          />
        )}
      </section>
    </div>
  );

  const rightColumn = (
    <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-[18px] pb-7 pt-3.5">
      <MediaCard
        kind="storyboard"
        projectName={projectName}
        segmentId={segmentId}
        assetPath={assets?.storyboard_image ?? null}
        aspectRatio={aspectRatio}
        hideGenerateButton={isGridMode}
        generating={generatingStoryboard}
        estimatedCost={sbEstimate ?? undefined}
        onGenerate={() => onGenerateStoryboard?.(segmentId)}
        onRestore={onRestoreStoryboard}
        generateDisabled={dirty || saving}
        generateDisabledHint={dirty ? dirtyHint : undefined}
      />
      <MediaCard
        kind="video"
        projectName={projectName}
        segmentId={segmentId}
        assetPath={assets?.video_clip ?? null}
        posterPath={assets?.video_thumbnail ?? null}
        aspectRatio={aspectRatio}
        generating={generatingVideo}
        generateDisabled={!hasStoryboard || dirty || saving}
        generateDisabledHint={dirty ? dirtyHint : undefined}
        estimatedCost={vidEstimate ?? undefined}
        onGenerate={() => onGenerateVideo?.(segmentId)}
        onRestore={onRestoreVideo}
      />
    </div>
  );

  const navDisabled = dirty || saving;

  return (
    <div
      className="flex min-h-0 min-w-0 flex-col overflow-hidden"
      style={{
        background:
          "radial-gradient(ellipse at top, oklch(0.20 0.012 270 / 0.35), oklch(0.17 0.010 265 / 0.2))",
      }}
    >
      <div
        className="relative flex items-center gap-2.5 px-5 py-3"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <span
          className="num rounded-md px-2.5 py-1 text-[12px] font-bold"
          style={{
            background:
              "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
            color: "oklch(0.14 0 0)",
            letterSpacing: "0.3px",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 2px 6px -2px var(--color-accent-glow)",
          }}
        >
          {segmentId}
        </span>
        <span
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-[3px] text-[11.5px]"
          style={{
            background: "oklch(0.22 0.011 265 / 0.6)",
            border: "1px solid var(--color-hairline-soft)",
            color: "var(--color-text-2)",
          }}
        >
          <span style={{ color: "var(--color-text-4)" }}>⏱</span>
          <span className="num">
            {t("duration_seconds_value_text", { value: segment.duration_seconds ?? 0 })}
          </span>
        </span>
        <StatusBadge status={status} />
        {sceneType && (
          <span
            className="inline-flex items-center gap-1.5 truncate text-[11.5px]"
            style={{ color: "var(--color-text-3)" }}
          >
            <span style={{ color: "var(--color-text-4)" }}>·</span>
            <span className="truncate">{sceneType}</span>
          </span>
        )}
        <span className="flex-1" />

        <div className="flex items-center gap-1.5">
          <span
            className="num text-[10.5px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("shot_detail_count", {
              current: selectedIndex + 1,
              total: totalCount,
            })}
          </span>
          <button
            type="button"
            onClick={onPrev}
            disabled={navDisabled}
            title={navDisabled ? dirtyHint : t("shot_detail_prev")}
            className="sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={t("shot_detail_prev")}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={navDisabled}
            title={navDisabled ? dirtyHint : t("shot_detail_next")}
            className="sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={t("shot_detail_next")}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <NotesDrawer
            shotId={segmentId}
            value={note}
            onCommit={handleNotesCommit}
          />
        </div>
      </div>

      {dirty && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 px-5 py-2"
          style={{
            background:
              "linear-gradient(180deg, var(--color-accent-dim), oklch(0.20 0.012 270 / 0.35))",
            borderBottom: "1px solid var(--color-accent-soft)",
          }}
        >
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "var(--color-accent)",
              boxShadow: "0 0 6px var(--color-accent-glow)",
            }}
          />
          <span
            className="num text-[10.5px] uppercase"
            style={{
              letterSpacing: "1.0px",
              color: "var(--color-accent-2)",
            }}
          >
            {t("shot_detail_unsaved")}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={handleCancel}
            disabled={saving}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              color: "var(--color-text-3)",
              border: "1px solid var(--color-hairline)",
              background: "oklch(0.22 0.011 265 / 0.5)",
            }}
            onMouseEnter={(e) => {
              if (!saving) {
                e.currentTarget.style.color = "var(--color-text)";
                e.currentTarget.style.background =
                  "oklch(0.26 0.013 265 / 0.7)";
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
            }}
          >
            <Undo2 className="h-3.5 w-3.5" />
            <span>{t("shot_detail_cancel")}</span>
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              color: "oklch(0.14 0 0)",
              background:
                "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
              boxShadow:
                "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -6px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
            }}
            onMouseEnter={(e) => {
              if (!saving) e.currentTarget.style.transform = "translateY(-1px)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = "translateY(0)";
            }}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="h-3.5 w-3.5" />
            )}
            <span>
              {saving ? t("shot_detail_saving") : t("shot_detail_save")}
            </span>
          </button>
        </div>
      )}

      <ResponsiveDetailGrid
        left={leftColumn}
        mid={midColumn}
        right={rightColumn}
      />
    </div>
  );
}
