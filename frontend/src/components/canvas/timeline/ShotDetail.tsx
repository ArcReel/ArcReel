import { useCallback, useEffect, useId, useMemo, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Film, ImageIcon, Timer } from "lucide-react";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import type {
  NarrationSegment,
  DramaScene,
  AdShot,
  ImagePrompt,
  VideoPrompt,
  Dialogue,
  Utterance,
} from "@/types";
import { AD_SECTION_VALUES } from "@/types";
import { ImagePromptEditor } from "./ImagePromptEditor";
import { VideoPromptEditor } from "./VideoPromptEditor";
import { DialogueListEditor } from "./DialogueListEditor";
import { UtteranceListEditor } from "./UtteranceListEditor";
import { SourceTextReadonly } from "@/components/shared/SourceTextReadonly";
import { ShotDetailLayout, ShotGroup, ShotMediaGrid, ShotSection, ShotSourceCollapsible } from "./ShotDetailLayout";
import { ShotDetailHeader } from "./ShotDetailHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { PartialSaveError, useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useStaysInEpisodeView } from "@/components/canvas/episode-page/EpisodeViewScope";
import { MediaCard } from "./MediaCard";
import { EndFrameRow } from "./EndFrameRow";
import { NarrationAudioCard } from "./NarrationAudioCard";
import { NotesDrawer } from "./NotesDrawer";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { ReferencesSection } from "./ReferencesSection";
import { StatusBadge, statusFromAssets } from "./StatusBadge";
import { ShotStructureActions, type InsertShotHandler } from "./ShotStructureActions";
import { SegmentBreakToggle } from "./SegmentBreakToggle";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, isScriptFileBusy } from "@/stores/tasks-store";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import { speakerCandidates } from "@/utils/plan-new-assets";
import { durationIncompatibleLabel } from "@/components/canvas/shared/PlanDurationSelect";
import { errMsg } from "@/utils/async";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { charactersFieldFor, type CharactersField } from "@/utils/script-shape";
import {
  emptyImagePrompt,
  emptyVideoPrompt,
  isStructuredImagePrompt,
  isStructuredVideoPrompt,
} from "@/utils/prompt-shape";
import { isContinuousIntegerRange } from "@/utils/duration_format";
import { PromptAuthoringButton } from "@/components/canvas/shared/PromptAuthoringButton";

type Segment = NarrationSegment | DramaScene | AdShot;
type DetailContentMode = "narration" | "drama" | "ad";

/** 提示词形态切换与预览按分镜图 / 视频两侧分别作用。 */
type PromptSide = "image" | "video";
/** `null` = 待编写：机械转换落盘的条目还没有这一侧提示词。 */
type ImagePromptValue = ImagePrompt | string | null;
type VideoPromptValue = VideoPrompt | string | null;

interface ShotDetailProps {
  segment: Segment;
  segmentId: string;
  contentMode: DetailContentMode;
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  /** 当前集号；给了才提供单条「编写提示词」入口 */
  episode?: number;
  /** 当前剧集剧本文件名，分镜图/视频自主上传需要它定位剧本条目 */
  scriptFile?: string;
  /** Total shot count for "1/N" indicator */
  selectedIndex: number;
  totalCount: number;
  onPrev: () => void;
  onNext: () => void;
  /**
   * 提交分镜字段的增量修改，失败时抛错。resolve 为保存后项目是否已刷新：
   * 为 false 时界面上的剧本还是旧的，「保存并生成」不能接着生成。缺省时只读展示。
   */
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>) => Promise<boolean>;
  /** 分镜改序：向前或向后移动一位 */
  onMoveShot?: (shotId: string, direction: "earlier" | "later") => void | Promise<void>;
  /** 分镜重排请求在途，移动按钮禁用 */
  movePending?: boolean;
  /** 在当前分镜之后新增分镜（旁白带正文），resolve 为是否成功；缺省时不渲染入口。 */
  onInsertShot?: InsertShotHandler;
  /** 移除当前分镜，resolve 为是否成功；缺省时不渲染入口。 */
  onRemoveShot?: (itemId: string) => Promise<boolean>;
  /** 分镜新增 / 移除请求在途，切镜与增删入口禁用 */
  structurePending?: boolean;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string) => void;
  /** 版本恢复与媒体上传之后刷新项目；resolve 为 false 时刷新没成功（父级已提示），不再报告成功。 */
  onRestoreStoryboard?: () => Promise<unknown> | void;
  onRestoreVideo?: () => Promise<unknown> | void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
  generatingNarration?: boolean;
  durationOptions?: number[];
  /** 档位为空是因为这一维由端点固定（workflow 自己定片长），不是型号没登记时长。 */
  durationEndpointFixed?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  /** 已保存时长越界的成因判定；缺省时退回不区分成因的通用警告文案。 */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  /** 分镜视图经它读当前分镜有无未保存修改、用 ⌘S / Ctrl+S 保存；卸载时清空。 */
  editRef?: RefObject<ShotEditHandle | null>;
}

/** 分镜视图需要的当前分镜编辑状态。 */
export interface ShotEditHandle {
  dirty: boolean;
  /** 保存当前分镜；保存在途或没有修改时什么都不做。 */
  save: () => void;
}

/**
 * 分镜详情的编辑单元：提示词、口播与台词、旁白正文、时长、备注、引用与切分点。
 * 这些字段都先进入未保存修改，由提示条统一保存或放弃。可选字段只在对应内容类型下存在。
 */
interface ShotFields {
  image_prompt: ImagePromptValue;
  video_prompt: VideoPromptValue;
  duration_seconds: number;
  note: string;
  characters: string[];
  scenes: string[];
  props: string[];
  /** 旁白 / 剧情演绎：章节切分点 */
  segment_break?: boolean;
  /** 广告 / 短片：口播文案与带货框架段落标签 */
  voiceover_text?: string;
  section?: string;
  /** 剧情演绎：分镜级有序发声序列（台词 + 画外音） */
  utterances?: Utterance[];
  /** 旁白 / 解说：旁白正文 */
  novel_text?: string;
}

const SHOT_FIELD_KEYS = [
  "image_prompt",
  "video_prompt",
  "duration_seconds",
  "note",
  "characters",
  "scenes",
  "props",
  "segment_break",
  "voiceover_text",
  "section",
  "utterances",
  "novel_text",
] as const satisfies readonly (keyof ShotFields)[];

type ShotFieldKey = (typeof SHOT_FIELD_KEYS)[number];

const EMPTY_UTTERANCES: Utterance[] = [];

// voiceover 的 speaker 允许缺省或 null，两种写法语义等价（无说话人）。比较前归一：
// voiceover speaker 统一为 null、并固定键序，避免 `{}` 与 `{ speaker: null }` 判成不同，
// 否则上游把画外音字段规范化后修改标记清不掉。
const canonicalUtterance = (u: Utterance): Utterance =>
  u.kind === "dialogue"
    ? { kind: "dialogue", speaker: u.speaker, text: u.text }
    : { kind: "voiceover", speaker: null, text: u.text };

/**
 * 字段的等值签名。字段集合稳定，JSON 即可：键序差异只会来自同一构造路径。
 * 尚无提示词（null）与空文本等价：既没有内容可保存，PATCH 也不接受清空提示词。
 */
function fieldSig(key: ShotFieldKey, value: unknown): string {
  if (key === "image_prompt" || key === "video_prompt") return JSON.stringify(value ?? "");
  if (key === "utterances") return JSON.stringify(((value as Utterance[] | undefined) ?? []).map(canonicalUtterance));
  return JSON.stringify(value ?? null);
}

function fieldEqual(key: ShotFieldKey, a: ShotFields, b: ShotFields): boolean {
  return a[key] === b[key] || fieldSig(key, a[key]) === fieldSig(key, b[key]);
}

function shotFieldsEqual(a: ShotFields, b: ShotFields): boolean {
  return a === b || SHOT_FIELD_KEYS.every((key) => fieldEqual(key, a, b));
}

/** 只提交改过的字段；角色引用按内容类型写回对应的字段名。 */
function shotPatch(value: ShotFields, saved: ShotFields, charField: CharactersField): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const key of SHOT_FIELD_KEYS) {
    if (fieldEqual(key, value, saved)) continue;
    patch[key === "characters" ? charField : key] = value[key];
  }
  return patch;
}

function shotFieldsOf(seg: Segment, mode: DetailContentMode): ShotFields {
  const base = {
    image_prompt: seg.image_prompt,
    video_prompt: seg.video_prompt,
    duration_seconds: seg.duration_seconds ?? 0,
    note: seg.note ?? "",
    scenes: seg.scenes ?? [],
    props: seg.props ?? [],
  };
  if (mode === "ad") {
    const shot = seg as AdShot;
    return {
      ...base,
      characters: shot.characters_in_shot ?? [],
      voiceover_text: shot.voiceover_text ?? "",
      section: shot.section ?? "",
    };
  }
  if (mode === "drama") {
    const scene = seg as DramaScene;
    return {
      ...base,
      characters: scene.characters_in_scene ?? [],
      segment_break: scene.segment_break === true,
      utterances: scene.utterances ?? EMPTY_UTTERANCES,
    };
  }
  const segment = seg as NarrationSegment;
  return {
    ...base,
    characters: segment.characters_in_segment ?? [],
    segment_break: segment.segment_break === true,
    novel_text: segment.novel_text ?? "",
  };
}

/** 配音的文本：旁白取正文，广告取口播；剧情演绎只在没有台词时取画外音。 */
function narrationTextOf(fields: ShotFields, mode: DetailContentMode): string {
  if (mode === "narration") return fields.novel_text ?? "";
  if (mode === "ad") return fields.voiceover_text ?? "";
  const utterances = fields.utterances ?? EMPTY_UTTERANCES;
  if (utterances.some((utterance) => utterance.kind === "dialogue")) return "";
  return utterances
    .filter((utterance) => utterance.kind === "voiceover")
    .map((utterance) => utterance.text.trim())
    .filter(Boolean)
    .join("\n");
}

/**
 * 时长是否被在跑的任务锁住：分镜图与视频任务已捕获旧时长，改了两边就不一致。
 * 宫格任务另按 scriptFile 判：它的 resource_id 是 grid_id，切割阶段会覆写本集内多个分镜。
 * 读 tasks-store 的当前值，不吃渲染之后才启动的任务。
 */
function durationLockedByTasks(projectName: string, segmentId: string, scriptFile?: string): boolean {
  return (
    isResourceBusy("storyboard", projectName, segmentId) ||
    isResourceBusy("video", projectName, segmentId) ||
    isScriptFileBusy("grid", scriptFile, projectName)
  );
}

interface DurationPillProps {
  seconds: number;
  segmentId: string;
  projectName: string;
  /** 本集剧本文件名；宫格任务按它做 scriptFile 粒度的占用判定。 */
  scriptFile?: string;
  durationOptions: number[];
  durationEndpointFixed?: boolean;
  durationWarningReason?: ShotDetailProps["durationWarningReason"];
  /** 选中的时长写进未保存修改；缺省时只读展示。 */
  onChange?: (seconds: number) => void;
  /** 该分镜有分镜图 / 视频任务在跑；置真时禁止改时长。 */
  busy?: boolean;
}

function DurationPill({
  seconds,
  segmentId,
  projectName,
  scriptFile,
  durationOptions,
  durationEndpointFixed = false,
  durationWarningReason,
  onChange,
  busy = false,
}: DurationPillProps) {
  const { t } = useTranslation("dashboard");
  const hintId = useId();
  const [open, setOpen] = useState(false);

  // 打开时与选中时都复核占用态：面板打开后任务可能才启动，busy prop 只反映上次渲染。
  const rejectIfBusy = useCallback(() => {
    if (!busy && !durationLockedByTasks(projectName, segmentId, scriptFile)) return false;
    useAppStore.getState().pushToast(t("duration_locked_generating"), "info");
    return true;
  }, [busy, projectName, segmentId, scriptFile, t]);

  const noOptions = durationOptions.length === 0;
  const locked = noOptions || busy;
  // 转入锁定态时真正收起面板，而不只是遮蔽：只派生可见性的话，任务结束后旧面板会自行重现。
  const [prevLocked, setPrevLocked] = useState(locked);
  if (locked !== prevLocked) {
    setPrevLocked(locked);
    if (locked) setOpen(false);
  }

  const isIncompatible = durationOptions.length > 0 && !durationOptions.includes(seconds);
  // 与项目默认时长的三种提示同一套判定（见 useModelCapabilities.durationOutOfRangeReason）。
  const incompatibleLabel = durationIncompatibleLabel(t, seconds, durationOptions, durationWarningReason?.(seconds));
  const useSlider = isContinuousIntegerRange(durationOptions) && durationOptions.length >= 5;

  const select = (next: number) => {
    if (rejectIfBusy()) {
      setOpen(false);
      return;
    }
    onChange?.(next);
  };

  const content = (
    <>
      <Timer aria-hidden className="size-3.5" />
      <span className="num">{t("duration_seconds_value_text", { value: seconds })}</span>
      {isIncompatible ? (
        <>
          <AlertTriangle aria-hidden className="size-3.5 text-warn" />
          <span className="sr-only">{incompatibleLabel}</span>
        </>
      ) : null}
    </>
  );

  if (!onChange) {
    return <span className="inline-flex items-center gap-1 text-xs text-subtle-foreground">{content}</span>;
  }

  if (locked) {
    const reason = busy
      ? t("duration_locked_generating")
      : t(durationEndpointFixed ? "duration_not_driven_notice" : "duration_no_options");
    return (
      <>
        <Tooltip>
          <TooltipTrigger render={<span className="inline-flex" />}>
            <Button variant="outline" size="xs" disabled aria-describedby={hintId}>
              {content}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{reason}</TooltipContent>
        </Tooltip>
        <span id={hintId} hidden>
          {reason}
        </span>
      </>
    );
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next && rejectIfBusy()) return;
        setOpen(next);
      }}
    >
      <PopoverTrigger render={<Button variant="outline" size="xs" />}>{content}</PopoverTrigger>
      {/* 弹层盖在编辑栏与媒体栏的控件上，fixed 定位与对话框同属浮层 */}
      <PopoverContent
        align="start"
        positionMethod="fixed"
        aria-label={t("duration_selector_aria")}
        className="w-auto max-w-80"
      >
        {isIncompatible ? (
          <p className="flex items-start gap-1.5 text-xs text-subtle-foreground">
            <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0 text-warn" />
            {incompatibleLabel}
          </p>
        ) : null}
        {useSlider ? (
          <div className="flex items-center gap-2">
            <input
              type="range"
              aria-label={t("duration_selector_aria")}
              aria-valuetext={t("duration_seconds_value_text", { value: seconds })}
              min={durationOptions[0]}
              max={durationOptions[durationOptions.length - 1]}
              step={1}
              value={seconds}
              onChange={(e) => select(Number(e.target.value))}
              className="w-40 accent-primary"
            />
            <span className="num min-w-9 text-right text-xs text-subtle-foreground">
              {t("duration_seconds_value_text", { value: seconds })}
            </span>
          </div>
        ) : (
          <div className="num flex flex-wrap gap-1" role="radiogroup" aria-label={t("duration_selector_aria")}>
            {durationOptions.map((d) => {
              const checked = d === seconds;
              return (
                <Button
                  key={d}
                  role="radio"
                  aria-checked={checked}
                  variant={checked ? "default" : "outline"}
                  size="xs"
                  onClick={() => {
                    select(d);
                    setOpen(false);
                  }}
                >
                  {t("duration_seconds_value_text", { value: d })}
                </Button>
              );
            })}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function ShotDetail({
  segment,
  segmentId,
  contentMode,
  aspectRatio,
  projectName,
  episode,
  scriptFile,
  selectedIndex,
  totalCount,
  onPrev,
  onNext,
  onUpdatePrompt,
  onMoveShot,
  movePending,
  onInsertShot,
  onRemoveShot,
  structurePending,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateNarration,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
  generatingNarration,
  durationOptions = [],
  durationEndpointFixed,
  lastFrame,
  capabilitiesLoading,
  durationWarningReason,
  editRef,
}: ShotDetailProps) {
  const { t } = useTranslation("dashboard");
  const status = statusFromAssets(segment.generated_assets?.status);
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));
  const isAd = contentMode === "ad";
  const isDrama = contentMode === "drama";
  const isNarration = contentMode === "narration";
  const readOnly = !onUpdatePrompt;
  const charField = charactersFieldFor(contentMode);

  // 父级 ShotSplitView 按 segmentId 作 key 挂载：切换分镜前由离开拦截询问。
  const source = useMemo(() => shotFieldsOf(segment, contentMode), [segment, contentMode]);
  const save = useCallback(
    async (value: ShotFields, saved: ShotFields) => {
      if (!onUpdatePrompt) return;
      const patch = shotPatch(value, saved, charField);
      if (Object.keys(patch).length === 0) return;
      // 在跑的任务已捕获旧时长：保存时再复核一次，不吃改完时长之后才启动的任务
      if (
        "duration_seconds" in patch &&
        (generatingStoryboard || generatingVideo || durationLockedByTasks(projectName, segmentId, scriptFile))
      ) {
        throw new Error(t("duration_locked_generating"));
      }
      const refreshed = await onUpdatePrompt(segmentId, patch);
      // 已落盘但界面上的剧本没刷新：不能当作保存成功，否则「保存并生成」会接着用旧剧本生成
      if (!refreshed) throw new PartialSaveError(t("shot_saved_not_refreshed"), { saved: value });
    },
    [onUpdatePrompt, charField, generatingStoryboard, generatingVideo, projectName, segmentId, scriptFile, t],
  );
  const allowNavigation = useStaysInEpisodeView();
  const unit = useEditUnit({
    source,
    save,
    isEqual: shotFieldsEqual,
    leaveTitle: t("shot_leave_title", { id: itemIdWithinEpisode(segmentId) }),
    allowNavigation,
  });
  const { value: fields, setValue, dirty } = unit;
  const saving = unit.status === "saving";
  // PATCH 已落盘但刷新失败时 savedValue 领先于剧本；直到新 source 到达才允许再生成或预览。
  const refreshPending = !shotFieldsEqual(source, unit.savedValue);
  const saveForPreview = unit.save;
  const preparePreview = useCallback(async () => !refreshPending && await saveForPreview(), [refreshPending, saveForPreview]);

  const saveUnit = unit.save;
  useEffect(() => {
    if (!editRef) return;
    editRef.current = {
      dirty,
      save: () => {
        if (!saving) void saveUnit();
      },
    };
    return () => {
      editRef.current = null;
    };
  }, [editRef, dirty, saveUnit, saving]);

  const setField = <K extends keyof ShotFields>(key: K, next: ShotFields[K]) =>
    setValue((prev) => ({ ...prev, [key]: next }));

  const narrationText = narrationTextOf(fields, contentMode);
  const hasNarrationText = narrationText.trim().length > 0;

  const [restoringMedia, setRestoringMedia] = useState(false);
  const [uploadingKind, setUploadingKind] = useState<"storyboard" | "video" | null>(null);
  const [endFrameSubmitting, setEndFrameSubmitting] = useState(false);
  const handleUpload = async (kind: "storyboard" | "video", file: File) => {
    // 单个分镜同时只允许一个上传：两张卡写同一后端资源族，避免并发覆写
    if (!scriptFile || uploadingKind || restoringMedia || isResourceBusy(kind, projectName, segmentId) || isScriptFileBusy("grid", scriptFile, projectName)) return;
    setUploadingKind(kind);
    try {
      const result = await API.uploadShotMedia(projectName, scriptFile, segmentId, kind, file);
      useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
      // 复用版本恢复的刷新管线（refreshProject 等由父级回调承载）；刷新失败时父级已提示，不同时报告成功
      const refreshed = kind === "storyboard" ? await onRestoreStoryboard?.() : await onRestoreVideo?.();
      if (refreshed === false) return;
      useAppStore.getState().pushToast(t("media_upload_success", { id: segmentId }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("media_upload_failed", { message: errMsg(err) }), "error");
    } finally {
      setUploadingKind(null);
    }
  };

  const projectCharacters = useProjectsStore((s) => s.currentProjectData?.characters);
  const speakerNames = useMemo(() => speakerCandidates(projectCharacters ?? {}), [projectCharacters]);

  const imgValue = isStructuredImagePrompt(fields.image_prompt) ? fields.image_prompt : null;
  const vidValue = isStructuredVideoPrompt(fields.video_prompt) ? fields.video_prompt : null;

  const handleImgUpdate = (patch: Partial<ImagePrompt>) => {
    setValue((prev) => {
      if (!isStructuredImagePrompt(prev.image_prompt)) return prev;
      const merged: ImagePrompt = {
        ...prev.image_prompt,
        ...patch,
        composition: { ...prev.image_prompt.composition, ...(patch.composition ?? {}) },
      };
      return { ...prev, image_prompt: merged };
    });
  };

  const handleVidUpdate = (patch: Partial<VideoPrompt>) => {
    setValue((prev) => {
      if (!isStructuredVideoPrompt(prev.video_prompt)) return prev;
      return { ...prev, video_prompt: { ...prev.video_prompt, ...patch } };
    });
  };

  const handleDialogueChange = (dialogue: Dialogue[]) => handleVidUpdate({ dialogue });

  // 提示词形态切换。结构化 → 文本以后端渲染结果为初值（前端不复刻渲染逻辑）；
  // 文本 → 结构化不做解析，须显式确认丢弃文本。
  const [formSwitching, setFormSwitching] = useState<PromptSide | null>(null);
  const [pendingStructSwitch, setPendingStructSwitch] = useState<PromptSide | null>(null);
  const [formSwitchError, setFormSwitchError] = useState<{ side: PromptSide; message: string } | null>(null);
  const formHintId = useId();

  const switchToTextForm = async (side: PromptSide) => {
    if (!scriptFile || formSwitching) return;
    setFormSwitching(side);
    setFormSwitchError(null);
    try {
      const preview = await API.previewScriptItemPrompts(projectName, segmentId, scriptFile);
      const rendered = side === "image" ? preview.storyboard_image : preview.video;
      if (rendered.text === null) {
        // 渲染不出最终文本（条目缺该提示词字段、或形状不合规）时不切换：以空正文落进文本形态，
        // 既丢掉了不可用的原因，也只会在保存时被后端的非空校验以无关文案拒掉。
        setFormSwitchError({ side, message: rendered.unavailable ?? t("prompt_form_switch_unavailable") });
        return;
      }
      const text = rendered.text;
      setField(side === "image" ? "image_prompt" : "video_prompt", text);
    } catch (e) {
      setFormSwitchError({ side, message: errMsg(e) });
    } finally {
      setFormSwitching(null);
    }
  };

  const renderFormSwitchError = (side: PromptSide) =>
    formSwitchError?.side === side ? <p className="text-xs text-warn">{formSwitchError.message}</p> : null;

  const confirmStructuredForm = () => {
    const side = pendingStructSwitch;
    if (!side) return;
    if (side === "image") setField("image_prompt", emptyImagePrompt());
    else setField("video_prompt", emptyVideoPrompt(isDrama));
    setPendingStructSwitch(null);
  };

  const renderFormToggle = (side: PromptSide, isStructured: boolean) => {
    // 结构化 → 文本的初值取自已保存内容，带着未保存修改切换会静默丢弃它们，须先保存；
    // 文本 → 结构化本就丢弃文本，由确认框把关。
    const needsSave = isStructured && dirty;
    const blocked = readOnly || !scriptFile || formSwitching !== null || needsSave;
    const current = isStructured ? "structured" : "text";
    const group = (
      <ToggleGroup
        aria-label={t("prompt_form_group_label")}
        aria-describedby={needsSave ? formHintId : undefined}
        variant="outline"
        size="sm"
        spacing={0}
        value={[current]}
        onValueChange={(next: string[]) => {
          // 再点当前形态会清空选择，忽略
          if (next[0] === "text") void switchToTextForm(side);
          else if (next[0] === "structured") setPendingStructSwitch(side);
        }}
      >
        <ToggleGroupItem value="structured" disabled={readOnly || (!isStructured && blocked)}>
          {t("prompt_form_structured")}
        </ToggleGroupItem>
        <ToggleGroupItem value="text" disabled={readOnly || (isStructured && blocked)}>
          {t("prompt_form_text")}
        </ToggleGroupItem>
      </ToggleGroup>
    );
    if (!needsSave) return group;
    return (
      <Tooltip>
        <TooltipTrigger render={<span className="inline-flex" />}>{group}</TooltipTrigger>
        <TooltipContent>{t("prompt_form_switch_needs_save")}</TooltipContent>
      </Tooltip>
    );
  };

  // 预览与生成共用已保存剧本；未保存修改必须先保存并刷新成功。
  const renderPromptPreview = (side: PromptSide) => {
    if (!scriptFile) return null;
    const previewSide = side === "image" ? "storyboard_image" : "video";
    return (
      <PromptPreviewButton
        title={t(side === "image" ? "prompt_preview_title_storyboard_image" : "prompt_preview_title_video")}
        load={async (signal) =>
          (await API.previewScriptItemPrompts(projectName, segmentId, scriptFile, { signal }))[previewSide]
        }
        beforeOpen={preparePreview}
        saveFirst={dirty}
        disabled={saving || refreshPending}
      />
    );
  };

  const sbEstimate = segCost?.estimate?.image;
  const vidEstimate = segCost?.estimate?.video;
  const narrationEstimate = segCost?.estimate?.audio;

  const assets = segment.generated_assets;
  const hasStoryboard = !!assets?.storyboard_image;

  // 展示用去重：products_in_shot 无唯一性约束（同一商品多次入画合法），重复名直接作 key 会撞
  const productNames = isAd ? Array.from(new Set((segment as AdShot).products_in_shot ?? [])) : [];

  // 引用弹窗的「确定」只把改动写进未保存修改，与其他字段一起保存
  const handleRefsApply = (patch: Record<string, string[]>) => {
    setValue((prev) => ({
      ...prev,
      ...(patch[charField] !== undefined ? { characters: patch[charField] } : {}),
      ...(patch.scenes !== undefined ? { scenes: patch.scenes } : {}),
      ...(patch.props !== undefined ? { props: patch.props } : {}),
    }));
  };

  // 广告/短片的段落标签、口播与商品与引用同属中栏顶部的「引用」组。
  const refsGroup = (
    <ShotGroup>
      {isAd && (
        <>
          <ShotSection title={t("detail_section_shot_section")} htmlFor={`shot-section-${segmentId}`}>
            <Input
              id={`shot-section-${segmentId}`}
              list={`shot-section-options-${segmentId}`}
              value={fields.section ?? ""}
              onChange={(e) => setField("section", e.target.value)}
              readOnly={readOnly}
              placeholder={t("detail_shot_section_placeholder")}
            />
            <datalist id={`shot-section-options-${segmentId}`}>
              {AD_SECTION_VALUES.map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
          </ShotSection>
          <ShotSection
            title={t("detail_section_voiceover")}
            htmlFor={`shot-voiceover-${segmentId}`}
            actions={
              <span className="num text-xs text-muted-foreground">
                {t("detail_field_chars_count", { count: (fields.voiceover_text ?? "").length })}
              </span>
            }
          >
            <Textarea
              id={`shot-voiceover-${segmentId}`}
              value={fields.voiceover_text ?? ""}
              onChange={(e) => setField("voiceover_text", e.target.value)}
              readOnly={readOnly}
              placeholder={t("detail_voiceover_placeholder")}
              className="max-h-none min-h-24"
            />
          </ShotSection>
          {productNames.length > 0 && (
            <ShotSection title={t("detail_section_products")}>
              <div className="flex flex-wrap gap-1.5">
                {productNames.map((name) => (
                  <Badge key={name} variant="outline">
                    {name}
                  </Badge>
                ))}
              </div>
            </ShotSection>
          )}
        </>
      )}
      <ReferencesSection
        projectName={projectName}
        contentMode={contentMode}
        characterNames={fields.characters}
        sceneNames={fields.scenes}
        propNames={fields.props}
        onSave={handleRefsApply}
        disabled={saving || readOnly}
      />
    </ShotGroup>
  );

  const promptsGroup = (
    <ShotGroup>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-subtle-foreground">{t("detail_section_prompts")}</h3>
        <span className="flex-1" />
        {episode !== undefined && (
          <PromptAuthoringButton projectName={projectName} episode={episode} scope="current" currentEntryId={segmentId} />
        )}
      </div>

      {segment.pending_authoring === true && (
        <div role="status" className="rounded-lg border border-border/50 bg-warn/5 px-3 py-2 text-xs text-subtle-foreground">
          {t("detail_pending_authoring_hint")}
        </div>
      )}

      <ShotSection
        title={t("detail_image_prompt_title")}
        icon={<ImageIcon aria-hidden className="size-3.5" />}
        actions={
          <>
            {imgValue && (
              <span className="num text-xs text-muted-foreground">
                {t("detail_field_chars_count", { count: imgValue.scene.length })}
              </span>
            )}
            {renderPromptPreview("image")}
            {renderFormToggle("image", imgValue !== null)}
          </>
        }
      >
        {imgValue ? (
          <ImagePromptEditor prompt={imgValue} onUpdate={handleImgUpdate} readOnly={readOnly} />
        ) : (
          <Textarea
            aria-label={t("detail_image_prompt_title")}
            value={typeof fields.image_prompt === "string" ? fields.image_prompt : ""}
            onChange={(e) => setField("image_prompt", e.target.value)}
            readOnly={readOnly}
            placeholder={t("detail_image_prompt_placeholder")}
            className="max-h-none min-h-31"
          />
        )}
        {renderFormSwitchError("image")}
      </ShotSection>

      <ShotSection
        title={t("detail_video_prompt_title")}
        icon={<Film aria-hidden className="size-3.5" />}
        actions={
          <>
            {vidValue && (
              <span className="num text-xs text-muted-foreground">
                {t("detail_field_chars_count", { count: vidValue.action.length })}
              </span>
            )}
            {renderPromptPreview("video")}
            {renderFormToggle("video", vidValue !== null)}
          </>
        }
      >
        {vidValue ? (
          <VideoPromptEditor prompt={vidValue} onUpdate={handleVidUpdate} readOnly={readOnly} />
        ) : (
          <Textarea
            aria-label={t("detail_video_prompt_title")}
            value={typeof fields.video_prompt === "string" ? fields.video_prompt : ""}
            onChange={(e) => setField("video_prompt", e.target.value)}
            readOnly={readOnly}
            placeholder={t("detail_video_prompt_placeholder")}
            className="max-h-none min-h-22"
          />
        )}
        {renderFormSwitchError("video")}
      </ShotSection>
      <span id={formHintId} hidden>
        {t("prompt_form_switch_needs_save")}
      </span>
    </ShotGroup>
  );

  // 台词：narration / ad 编辑扁平 video_prompt.dialogue；drama 编辑分镜级 utterances（台词 + 画外音）。
  const speechGroup = (
    <ShotGroup>
      {isDrama ? (
        <ShotSection title={t("detail_section_utterances")}>
          <UtteranceListEditor
            utterances={fields.utterances ?? EMPTY_UTTERANCES}
            onChange={(utterances) => setField("utterances", utterances)}
            disabled={saving || readOnly}
            speakerCandidates={speakerNames}
          />
        </ShotSection>
      ) : (
        <ShotSection title={t("detail_section_dialogue")}>
          {vidValue ? (
            <DialogueListEditor dialogue={vidValue.dialogue ?? []} onChange={handleDialogueChange} readOnly={readOnly} />
          ) : (
            <p className="rounded-lg border border-dashed border-border py-3 text-center text-xs text-muted-foreground">
              {t("detail_dialogue_empty")}
            </p>
          )}
        </ShotSection>
      )}
      {isNarration && (
        <ShotSection
          title={t("detail_section_narration_text")}
          htmlFor={`shot-narration-text-${segmentId}`}
          actions={
            <span className="num text-xs text-muted-foreground">
              {t("detail_field_chars_count", { count: (fields.novel_text ?? "").length })}
            </span>
          }
        >
          {/* 旁白正文用衬线字体，与配音卡里的正文一致；字体由外层继承 */}
          <div className="display-serif">
            <Textarea
              id={`shot-narration-text-${segmentId}`}
              value={fields.novel_text ?? ""}
              onChange={(e) => setField("novel_text", e.target.value)}
              readOnly={readOnly}
              placeholder={t("detail_narration_text_placeholder")}
              className="max-h-none min-h-30"
            />
          </div>
        </ShotSection>
      )}
    </ShotGroup>
  );

  const savedNarrationText = narrationTextOf(unit.savedValue, contentMode).trim();
  const hasSource = isDrama || (isAd && savedNarrationText.length > 0);
  const sourceGroup = hasSource ? (
    <ShotGroup>
      <ShotSourceCollapsible>
        {isDrama && <SourceTextReadonly text={(segment as DramaScene).source_text} />}
        {isAd && savedNarrationText && (
          <ShotSection title={t("detail_section_novel")}>
            <p className="display-serif max-w-[40em] border-l-2 border-primary/25 pl-3 text-sm leading-relaxed whitespace-pre-wrap text-foreground">
              {savedNarrationText}
            </p>
          </ShotSection>
        )}
      </ShotSourceCollapsible>
    </ShotGroup>
  ) : null;

  // 有未保存修改时生成按钮先保存再生成；保存失败不生成
  const generateLabel = dirty ? t("common:save_and_generate") : undefined;
  const savingHint = saving ? t("common:save_status_saving") : undefined;

  const storyboardMedia = (
    <MediaCard
      kind="storyboard"
      projectName={projectName}
      segmentId={segmentId}
      assetPath={assets?.storyboard_image ?? null}
      aspectRatio={aspectRatio}
      generating={generatingStoryboard}
      estimatedCost={sbEstimate ?? undefined}
      onGenerate={
        onGenerateStoryboard ? () => void unit.saveAndGenerate(() => onGenerateStoryboard(segmentId)) : undefined
      }
      generateLabel={generateLabel}
      restoring={restoringMedia}
      onRestoringChange={setRestoringMedia}
      checkBusy={() => uploadingKind !== null || saving || isScriptFileBusy("grid", scriptFile, projectName)}
      onRestore={onRestoreStoryboard}
      onUpload={scriptFile && !readOnly ? (file) => handleUpload("storyboard", file) : undefined}
      uploading={uploadingKind === "storyboard"}
      uploadDisabled={uploadingKind !== null}
      editScriptFile={readOnly ? undefined : scriptFile}
      generateDisabled={saving || refreshPending}
      generateDisabledHint={savingHint}
    />
  );

  const videoMedia = (
    <>
      {scriptFile && onGenerateVideo && (
        <EndFrameRow
          projectName={projectName}
          lastFrame={lastFrame}
          capabilitiesLoading={capabilitiesLoading}
          segmentId={segmentId}
          scriptFile={scriptFile}
          contentMode={contentMode}
          aspectRatio={aspectRatio}
          endFramePath={segment.end_frame_image ?? null}
          readOnly={readOnly}
          onSubmittingChange={setEndFrameSubmitting}
          videoUploadBusy={uploadingKind === "video"}
          shotSaving={saving}
        />
      )}
      <MediaCard
        kind="video"
        projectName={projectName}
        segmentId={segmentId}
        assetPath={assets?.video_clip ?? null}
        posterPath={assets?.video_thumbnail ?? null}
        aspectRatio={aspectRatio}
        generating={generatingVideo}
        generateDisabled={!hasStoryboard || saving || refreshPending}
        generateDisabledHint={hasStoryboard ? savingHint : undefined}
        estimatedCost={vidEstimate ?? undefined}
        onGenerate={onGenerateVideo ? () => void unit.saveAndGenerate(() => onGenerateVideo(segmentId)) : undefined}
        generateLabel={generateLabel}
        restoring={restoringMedia}
        onRestoringChange={setRestoringMedia}
        checkBusy={() => uploadingKind !== null || saving || endFrameSubmitting || isScriptFileBusy("grid", scriptFile, projectName)}
        onRestore={onRestoreVideo}
        onUpload={scriptFile && !readOnly ? (file) => handleUpload("video", file) : undefined}
        uploading={uploadingKind === "video"}
        uploadDisabled={uploadingKind !== null || endFrameSubmitting}
      />
    </>
  );

  const audioMedia =
    isNarration || hasNarrationText || Boolean(assets?.narration_audio) ? (
      <NarrationAudioCard
        readOnly={readOnly}
        projectName={projectName}
        segmentId={segmentId}
        novelText={narrationText}
        assetPath={assets?.narration_audio ?? null}
        generating={generatingNarration}
        generateDisabled={!hasNarrationText || saving || refreshPending}
        generateDisabledHint={!hasNarrationText ? t("no_original_text") : savingHint}
        generateLabel={generateLabel}
        estimatedCost={narrationEstimate ?? undefined}
        onGenerate={
          onGenerateNarration ? () => void unit.saveAndGenerate(() => onGenerateNarration(segmentId)) : undefined
        }
      />
    ) : null;

  // 重排在途也要锁定切镜：ShotSplitView 在移动完成回调里按当前 selectedIndex 偏移，
  // 在途切换分镜会让偏移作用到新选中项，选中态跳到错误分镜。有未保存修改时切镜由离开拦截询问。
  const navDisabled = !!movePending || !!structurePending;
  const navDisabledHint = movePending
    ? t("shot_move_pending")
    : structurePending
      ? t("shot_structure_pending")
      : undefined;
  // 增删会刷新整份剧本并改变选中项，有未保存修改时先保存或放弃
  const structureDisabled = navDisabled || dirty || saving;
  const structureDisabledHint = navDisabledHint ?? (dirty || saving ? t("shot_detail_save_first") : undefined);
  const durationBusy = !!generatingStoryboard || !!generatingVideo;

  const header = (
    <ShotDetailHeader
      segmentId={segmentId}
      index={selectedIndex}
      total={totalCount}
      meta={
        <>
          <DurationPill
            seconds={fields.duration_seconds}
            segmentId={segmentId}
            projectName={projectName}
            scriptFile={scriptFile}
            durationOptions={durationOptions}
            durationEndpointFixed={durationEndpointFixed}
            durationWarningReason={durationWarningReason}
            onChange={readOnly ? undefined : (seconds) => setField("duration_seconds", seconds)}
            busy={durationBusy}
          />
          <StatusBadge status={status} />
          {(isNarration || isDrama) && !readOnly && (
            <SegmentBreakToggle
              checked={fields.segment_break === true}
              onChange={(next) => setField("segment_break", next)}
              disabled={durationBusy}
            />
          )}
        </>
      }
      onMoveEarlier={onMoveShot ? () => void onMoveShot(segmentId, "earlier") : undefined}
      onMoveLater={onMoveShot ? () => void onMoveShot(segmentId, "later") : undefined}
      structureActions={
        <ShotStructureActions
          segmentId={segmentId}
          contentMode={contentMode}
          disabled={structureDisabled}
          disabledHint={structureDisabledHint}
          removeBlockedHint={
            generatingStoryboard || generatingVideo || generatingNarration
              ? t("shot_remove_blocked_generating")
              : totalCount <= 1
                ? t("shot_remove_blocked_last")
                : undefined
          }
          onInsert={onInsertShot}
          onRemove={onRemoveShot}
        />
      }
      onPrev={onPrev}
      onNext={onNext}
      navDisabled={navDisabled}
      navDisabledHint={navDisabledHint}
      notes={
        // 备注只有落库才有意义：只读展示下不给入口，免得输入的备注静默丢弃
        readOnly ? null : (
          <NotesDrawer shotId={segmentId} value={fields.note} onChange={(note) => setField("note", note)} />
        )
      }
    />
  );

  return (
    <>
      <ShotDetailLayout
        header={header}
        footer={<UnsavedChangesBar unit={unit} className="mx-5 my-3 shrink-0" />}
        main={
          <>
            {refsGroup}
            {promptsGroup}
            {speechGroup}
            {sourceGroup}
          </>
        }
        media={
          <ShotMediaGrid aspectRatio={aspectRatio} storyboard={storyboardMedia} video={videoMedia} audio={audioMedia} />
        }
      />
      <AlertDialog open={pendingStructSwitch !== null} onOpenChange={(next) => !next && setPendingStructSwitch(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("prompt_form_to_structured_title")}</AlertDialogTitle>
            <AlertDialogDescription>{t("prompt_form_to_structured_desc")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmStructuredForm}>
              {t("prompt_form_to_structured_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
