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
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import { statusFromAssets } from "./shot-status";
import { StatusBadge } from "./StatusBadge";
import { ShotStructureActions, type InsertShotHandler } from "./ShotStructureActions";
import { SegmentBreakToggle } from "./SegmentBreakToggle";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, isScriptFileBusy } from "@/stores/tasks-store";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import { speakerCandidates } from "@/utils/plan-new-assets";
import { durationIncompatibleLabel } from "@/components/canvas/shared/duration-incompatible";
import { errMsg } from "@/utils/async";
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";
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

/** Prompt-form switching/preview act separately on storyboard-image and video sides. */
type PromptSide = "image" | "video";
/** `null` means pending authoring: mechanically materialized entries lack this side's prompt. */
type ImagePromptValue = ImagePrompt | string | null;
type VideoPromptValue = VideoPrompt | string | null;

interface ShotDetailProps {
  segment: Segment;
  segmentId: string;
  contentMode: DetailContentMode;
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  /** Current episode ID; supplied enables per-shot prompt-authoring entries. */
  episode?: number;
  /** Current script filename locates entries for manual storyboard/video uploads. */
  scriptFile?: string;
  /** Total shot count for "1/N" indicator */
  selectedIndex: number;
  totalCount: number;
  onPrev: () => void;
  onNext: () => void;
  /**
   * Submit incremental shot-field edits, throwing on failure. Resolve whether project data refreshed after saving:
   * false leaves old scripts in UI, preventing save-and-generate continuation. Omitted means read-only.
   */
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>) => Promise<boolean>;
  /** Move shots one position earlier/later. */
  onMoveShot?: (shotId: string, direction: "earlier" | "later") => void | Promise<void>;
  /** In-flight reordering disables move buttons. */
  movePending?: boolean;
  /** Add after current shot, including narration bodies; resolves success, omitted hides entry. */
  onInsertShot?: InsertShotHandler;
  /** Remove current shot; resolves success, omitted hides entry. */
  onRemoveShot?: (itemId: string) => Promise<boolean>;
  /** Add/remove requests disable shot switching and mutation entries. */
  structurePending?: boolean;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string) => void;
  /** Refresh after version restore/media upload; false means failed refresh already reported by parents, so do not report success too. */
  onRestoreStoryboard?: () => Promise<unknown> | void;
  onRestoreVideo?: () => Promise<unknown> | void;
  generatingStoryboard?: boolean;
  generatingVideo?: boolean;
  generatingNarration?: boolean;
  durationOptions?: number[];
  /** Empty tiers mean endpoint-fixed/workflow-owned duration, not missing model durations. */
  durationEndpointFixed?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  /** Saved out-of-range cause classification; omitted uses generic warnings. */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  /** Shot view reads dirty state/saves via ⌘S / Ctrl+S here; clear on unmount. */
  editRef?: RefObject<ShotEditHandle | null>;
}

/** Current shot editing state needed by shot views. */
export interface ShotEditHandle {
  dirty: boolean;
  /** Save current shot; do nothing while saving or without changes. */
  save: () => void;
}

/**
 * Shot-detail editing unit: prompts, spoken copy/dialogue, narration body, duration, notes, references, and breaks.
 * All enter unsaved edits, saved/discarded together in notice bars. Optional fields exist only for matching content types.
 */
interface ShotFields {
  image_prompt: ImagePromptValue;
  video_prompt: VideoPromptValue;
  duration_seconds: number;
  note: string;
  characters: string[];
  scenes: string[];
  props: string[];
  /** Narration/drama chapter breaks. */
  segment_break?: boolean;
  /** Ad/short-film spoken copy and sales-framework section labels. */
  voiceover_text?: string;
  section?: string;
  /** Drama shot-level ordered speech: dialogue plus voiceover. */
  utterances?: Utterance[];
  /** Narration/commentary body. */
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

// Voiceover speakers may be omitted/null, semantically equivalent without speakers. Normalize before comparison:
// voiceover speakers to null and fixed key order, avoiding distinctions between `{}` / `{ speaker: null }`;
// otherwise upstream voiceover normalization would leave dirty markers uncleared.
const canonicalUtterance = (u: Utterance): Utterance =>
  u.kind === "dialogue"
    ? { kind: "dialogue", speaker: u.speaker, text: u.text }
    : { kind: "voiceover", speaker: null, text: u.text };

/**
 * Field equality signature. Stable field sets need only JSON; key-order differences come from the same construction path.
 * Missing prompts (null) equal empty text: nothing to save and PATCH does not accept clearing prompts.
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

/** Submit changed fields only; character references use content-type-specific field names. */
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

/** Voiceover text: narration bodies, ad spoken copy; drama voiceover only without dialogue. */
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
 * Whether active tasks lock durations: storyboard/video tasks captured old durations, so edits would mismatch both sides.
 * Grid occupancy uses scriptFile because resource_id is grid_id and splits overwrite multiple episode storyboards.
 * Read current tasks-store values to catch tasks starting after render.
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
  /** Episode script filename for grid scriptFile-level occupancy. */
  scriptFile?: string;
  durationOptions: number[];
  durationEndpointFixed?: boolean;
  durationWarningReason?: ShotDetailProps["durationWarningReason"];
  /** Selected duration enters unsaved edits; omitted means read-only. */
  onChange?: (seconds: number) => void;
  /** Running storyboard/video tasks for this shot prohibit duration edits. */
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

  // Recheck on opening/selection: tasks may start after opening; busy props reflect last renders only.
  const rejectIfBusy = useCallback(() => {
    if (!busy && !durationLockedByTasks(projectName, segmentId, scriptFile)) return false;
    useAppStore.getState().pushToast(t("duration_locked_generating"), "info");
    return true;
  }, [busy, projectName, segmentId, scriptFile, t]);

  const noOptions = durationOptions.length === 0;
  const locked = noOptions || busy;
  // Actually close on locking, not just hide; derived visibility alone would reopen old panels after tasks finish.
  const [prevLocked, setPrevLocked] = useState(locked);
  if (locked !== prevLocked) {
    setPrevLocked(locked);
    if (locked) setOpen(false);
  }

  const isIncompatible = durationOptions.length > 0 && !durationOptions.includes(seconds);
  // Share three-way project-default duration reasons (useModelCapabilities.durationOutOfRangeReason).
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
      {/* Overlay covers editor/media controls; fixed positioning shares dialog overlay layers. */}
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
  const itemLabel = useItemPositionLabel(segmentId);
  const status = statusFromAssets(segment.generated_assets?.status);
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));
  const isAd = contentMode === "ad";
  const isDrama = contentMode === "drama";
  const isNarration = contentMode === "narration";
  const readOnly = !onUpdatePrompt;
  const charField = charactersFieldFor(contentMode);

  // Parent ShotSplitView keys by segmentId; leave guards ask before switching shots.
  const source = useMemo(() => shotFieldsOf(segment, contentMode), [segment, contentMode]);
  const save = useCallback(
    async (value: ShotFields, saved: ShotFields) => {
      if (!onUpdatePrompt) return;
      const patch = shotPatch(value, saved, charField);
      if (Object.keys(patch).length === 0) return;
      // Running tasks captured old durations; recheck on save to catch tasks starting after duration edits.
      if (
        "duration_seconds" in patch &&
        (generatingStoryboard || generatingVideo || durationLockedByTasks(projectName, segmentId, scriptFile))
      ) {
        throw new Error(t("duration_locked_generating"));
      }
      const refreshed = await onUpdatePrompt(segmentId, patch);
      // Persisted but unrefreshed scripts do not count as successful saves, or save-and-generate would use old scripts.
      if (!refreshed) throw new PartialSaveError(t("shot_saved_not_refreshed"), { saved: value });
    },
    [onUpdatePrompt, charField, generatingStoryboard, generatingVideo, projectName, segmentId, scriptFile, t],
  );
  const allowNavigation = useStaysInEpisodeView();
  const unit = useEditUnit({
    source,
    save,
    isEqual: shotFieldsEqual,
    leaveTitle: t("shot_leave_title", { id: itemLabel }),
    allowNavigation,
  });
  const { value: fields, setValue, dirty } = unit;
  const saving = unit.status === "saving";
  // Structured→text request replaces the whole prompt side on return, overwriting edits made meanwhile.
  // Occupy editing units like saves, disabling structured editors/all save entries together.
  const [formSwitching, setFormSwitching] = useState<PromptSide | null>(null);
  const editBusy = saving || formSwitching !== null;
  // Persisted PATCH with failed refresh puts savedValue ahead of scripts; forbid generation/preview until new source arrives.
  const refreshPending = !shotFieldsEqual(source, unit.savedValue);
  const saveForPreview = unit.save;
  const preparePreview = useCallback(async () => !refreshPending && await saveForPreview(), [refreshPending, saveForPreview]);

  const saveUnit = unit.save;
  useEffect(() => {
    if (!editRef) return;
    editRef.current = {
      dirty,
      save: () => {
        if (!editBusy) void saveUnit();
      },
    };
    return () => {
      editRef.current = null;
    };
  }, [editRef, dirty, saveUnit, editBusy]);

  const setField = <K extends keyof ShotFields>(key: K, next: ShotFields[K]) =>
    setValue((prev) => ({ ...prev, [key]: next }));

  const narrationText = narrationTextOf(fields, contentMode);
  const hasNarrationText = narrationText.trim().length > 0;

  const [restoringMedia, setRestoringMedia] = useState(false);
  const [uploadingKind, setUploadingKind] = useState<"storyboard" | "video" | null>(null);
  const [endFrameSubmitting, setEndFrameSubmitting] = useState(false);
  const handleUpload = async (kind: "storyboard" | "video", file: File) => {
    // Permit one upload per shot: both cards write one backend resource family, avoiding concurrent overwrites.
    if (!scriptFile || uploadingKind || restoringMedia || isResourceBusy(kind, projectName, segmentId) || isScriptFileBusy("grid", scriptFile, projectName)) return;
    setUploadingKind(kind);
    try {
      const result = await API.uploadShotMedia(projectName, scriptFile, segmentId, kind, file);
      useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
      // Reuse restoration refresh pipelines through parent callbacks such as refreshProject; parents already report failures, never success too.
      const refreshed = kind === "storyboard" ? await onRestoreStoryboard?.() : await onRestoreVideo?.();
      if (refreshed === false) return;
      useAppStore.getState().pushToast(t("media_upload_success", { id: itemLabel }), "success");
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

  // Prompt forms: structured→text initializes from backend rendering without frontend reimplementation;
  // text→structured does not parse and requires explicit discard confirmation.
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
        // If final text cannot render (missing prompt/invalid shape), do not switch. Empty text forms would
        // lose unavailability reasons and merely fail backend nonempty validation on saving with unrelated wording.
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
    // Structured→text initializes saved content, so unsaved changes would silently disappear; save first;
    // text→structured inherently discards text, guarded by confirmation.
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
          // Clicking current forms would clear selection; ignore.
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

  // Preview/generation share saved scripts; unsaved edits must save and refresh successfully first.
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
        disabled={editBusy || refreshPending}
      />
    );
  };

  const sbEstimate = segCost?.estimate?.image;
  const vidEstimate = segCost?.estimate?.video;
  const narrationEstimate = segCost?.estimate?.audio;

  const assets = segment.generated_assets;
  const hasStoryboard = !!assets?.storyboard_image;

  // Deduplicate display keys: products_in_shot permits repeats when products appear multiple times, so raw names collide.
  const productNames = isAd ? Array.from(new Set((segment as AdShot).products_in_shot ?? [])) : [];

  // Reference confirmation only enters unsaved edits, saved together with other fields.
  const handleRefsApply = (patch: Record<string, string[]>) => {
    setValue((prev) => ({
      ...prev,
      ...(patch[charField] !== undefined ? { characters: patch[charField] } : {}),
      ...(patch.scenes !== undefined ? { scenes: patch.scenes } : {}),
      ...(patch.props !== undefined ? { props: patch.props } : {}),
    }));
  };

  // Ad/short-film section labels/spoken copy/products share top-middle reference groups.
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
        disabled={editBusy || readOnly}
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
          <ImagePromptEditor prompt={imgValue} onUpdate={handleImgUpdate} readOnly={readOnly || formSwitching === "image"} />
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
          <VideoPromptEditor prompt={vidValue} onUpdate={handleVidUpdate} readOnly={readOnly || formSwitching === "video"} />
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

  // Dialogue: ad edits flat video_prompt.dialogue, drama edits shot-level utterances (dialogue/voiceover).
  // Narration omits dialogue editors because narration bodies are voiceover and coexistence means mixed speech; list existing dialogue read-only with clearing.
  // Text-form drafts retain dialogue in rendered text, not counted as clearing.
  const savedLegacyDialogue = isNarration && isStructuredVideoPrompt(unit.savedValue.video_prompt)
    ? unit.savedValue.video_prompt.dialogue ?? []
    : [];
  const legacyDialogue = isNarration ? vidValue?.dialogue ?? [] : [];
  const legacyDialogueCleared = vidValue !== null && savedLegacyDialogue.length > 0 && legacyDialogue.length === 0;
  const speechGroup = (
    <ShotGroup>
      {isDrama ? (
        <ShotSection title={t("detail_section_utterances")}>
          <UtteranceListEditor
            utterances={fields.utterances ?? EMPTY_UTTERANCES}
            onChange={(utterances) => setField("utterances", utterances)}
            disabled={editBusy || readOnly}
            speakerCandidates={speakerNames}
          />
        </ShotSection>
      ) : isNarration ? (
        (legacyDialogue.length > 0 || legacyDialogueCleared) && (
          <ShotSection
            title={t("detail_section_dialogue")}
            actions={
              legacyDialogue.length > 0 && !readOnly ? (
                <Button variant="outline" size="xs" disabled={editBusy} onClick={() => handleDialogueChange([])}>
                  {t("detail_legacy_dialogue_clear")}
                </Button>
              ) : undefined
            }
          >
            {legacyDialogue.length > 0 ? (
              <>
                <p className="mb-2 text-xs text-muted-foreground">{t("detail_legacy_dialogue_hint")}</p>
                <ul className="flex flex-col gap-1.5">
                  {legacyDialogue.map((d, i) => (
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Legacy dialogue is read-only/whole-clear only, without reorder/add/remove, row state, or entry IDs
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <TruncatedText text={d.speaker} className="w-20 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 wrap-break-word whitespace-pre-wrap">{d.line}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{t("detail_legacy_dialogue_cleared")}</p>
            )}
          </ShotSection>
        )
      ) : (
        <ShotSection title={t("detail_section_dialogue")}>
          {vidValue ? (
            <DialogueListEditor
              dialogue={vidValue.dialogue ?? []}
              onChange={handleDialogueChange}
              readOnly={readOnly || formSwitching === "video"}
            />
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
          {/* Narration body uses serif matching voiceover-card text, inherited from outside. */}
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

  // Dirty generation saves first; failed saves never generate.
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
      generateDisabled={editBusy || refreshPending}
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
        generateDisabled={!hasStoryboard || editBusy || refreshPending}
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
        generateDisabled={!hasNarrationText || editBusy || refreshPending}
        generateDisabledHint={!hasNarrationText ? t("no_original_text") : savingHint}
        generateLabel={generateLabel}
        estimatedCost={narrationEstimate ?? undefined}
        onGenerate={
          onGenerateNarration ? () => void unit.saveAndGenerate(() => onGenerateNarration(segmentId)) : undefined
        }
      />
    ) : null;

  // In-flight reorder also locks switching: ShotSplitView completion offsets current selectedIndex,
  // so switching meanwhile would offset new selections to wrong shots. Dirty switching invokes leave guards.
  const navDisabled = !!movePending || !!structurePending;
  const navDisabledHint = movePending
    ? t("shot_move_pending")
    : structurePending
      ? t("shot_structure_pending")
      : undefined;
  // Add/remove refresh whole scripts/change selections; save or discard dirty edits first.
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
        // Notes matter only if persisted; read-only displays omit inputs rather than silently discarding entered notes.
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
        footer={<UnsavedChangesBar unit={unit} busy={formSwitching !== null} className="mx-5 my-3 shrink-0" />}
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
