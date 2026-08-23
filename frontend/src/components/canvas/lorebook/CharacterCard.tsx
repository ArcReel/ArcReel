import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Pause, Play, Upload, User, X } from "lucide-react";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import {
  ProjectAssetImageUsageSwitch,
  ProjectAssetLinkControl,
  ProjectAssetVoiceSourceSwitch,
} from "@/components/assets/ProjectAssetLinkControl";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageModelSelect, imageSelectionFromValue } from "@/components/shared/ImageModelSelect";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { EditableAssetName } from "./EditableAssetName";
import { ProjectAssetDeleteButton } from "./ProjectAssetDeleteButton";
import { VoiceSampleButton } from "./VoiceSampleButton";
import type { Character, ImageModelSelection } from "@/types";
import type { Asset } from "@/types/asset";

interface CharacterSavePayload {
  description: string;
  voiceStyle: string;
  referenceFile?: File | null;
  audioFile?: File | null;
}

/** mm:ss；无法确定时长（尚未加载完 metadata）时占位 --:--。 */
function formatAudioDuration(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface CharacterCardProps {
  name: string;
  character: Character;
  projectName: string;
  onSave: (name: string, payload: CharacterSavePayload) => Promise<void>;
  onGenerate: (name: string, selection?: ImageModelSelection) => void;
  onRestoreVersion?: () => Promise<void> | void;
  onReload?: () => Promise<unknown> | void;
  generating?: boolean;
  /** 只读展示（引导演示项目）：所有改写入口不渲染，文本字段不可编辑。 */
  readOnly?: boolean;
}

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

export function CharacterCard({
  name,
  character,
  projectName,
  onSave,
  onGenerate,
  onRestoreVersion,
  onReload,
  generating = false,
  readOnly = false,
}: CharacterCardProps) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const sheetFp = useProjectsStore(
    (s) => character.character_sheet ? s.getAssetFingerprint(character.character_sheet) : null,
  );
  const referenceFp = useProjectsStore(
    (s) => character.reference_image ? s.getAssetFingerprint(character.reference_image) : null,
  );
  const audioFp = useProjectsStore(
    (s) => character.reference_audio ? s.getAssetFingerprint(character.reference_audio) : null,
  );
  const [description, setDescription] = useState(character.description);
  const [voiceStyle, setVoiceStyle] = useState(character.voice_style ?? "");
  const [imgError, setImgError] = useState(false);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioPreview, setAudioPreview] = useState<string | null>(null);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [audioProgress, setAudioProgress] = useState(0);
  const [audioDuration, setAudioDuration] = useState<number | null>(null);
  const [deletingAudio, setDeletingAudio] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingSheet, setUploadingSheet] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [imageModel, setImageModel] = useState("");
  const [linkedAsset, setLinkedAsset] = useState<Asset | null>(null);
  const handleLinkedAssetResolved = useCallback((asset: Asset | null) => setLinkedAsset(asset), []);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const audioElRef = useRef<HTMLAudioElement>(null);
  const sheetInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();
  const voiceStyleId = useId();

  const handleSheetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (rejectIfAssetBusy("character", projectName, name, t)) return;
    setUploadingSheet(true);
    try {
      await API.uploadFile(projectName, "character", file, name);
      await onReload?.();
      useAppStore.getState().pushToast(t("assets:upload_sheet_success", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingSheet(false);
    }
  };

  useEffect(() => {
    // 上游角色变化时同步本地草稿字段
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(character.description);
    setVoiceStyle(character.voice_style ?? "");
  }, [character.description, character.voice_style]);

  useEffect(() => {
    // 角色立绘变化时重置图片加载错误标记
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setImgError(false);
  }, [character.character_sheet, sheetFp]);

  useEffect(() => {
    // 上游参考图变化时清空本地未提交的上传文件 + 释放 blob URL
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setReferenceFile(null);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [character.reference_image]);

  useEffect(() => {
    return () => {
      if (referencePreview) {
        URL.revokeObjectURL(referencePreview);
      }
    };
  }, [referencePreview]);

  useEffect(() => {
    // 上游参考音频变化时清空本地未提交的上传文件 + 释放 blob URL；
    // 同扩展名替换（如 wav 换 wav）路径字符串不变，靠 audioFp 才能感知内容已更新
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAudioFile(null);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [character.reference_audio, audioFp]);

  useEffect(() => {
    return () => {
      if (audioPreview) {
        URL.revokeObjectURL(audioPreview);
      }
    };
  }, [audioPreview]);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    autoResize();
  }, [autoResize, description]);

  const isDirty =
    description !== character.description ||
    voiceStyle !== (character.voice_style ?? "") ||
    referenceFile !== null ||
    audioFile !== null;

  const handleReferenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setReferenceFile(file);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    e.target.value = "";
  };

  const clearPendingReference = () => {
    setReferenceFile(null);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleAudioChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setAudioFile(file);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    e.target.value = "";
  };

  const clearPendingAudio = () => {
    setAudioFile(null);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (audioInputRef.current) {
      audioInputRef.current.value = "";
    }
  };

  const handleDeleteAudio = async () => {
    if (audioFile) {
      clearPendingAudio();
      return;
    }
    if (!character.reference_audio) return;
    if (rejectIfAssetBusy("character", projectName, name, t, "assets:delete_audio_busy_hint")) return;
    setDeletingAudio(true);
    try {
      await API.deleteCharacterReferenceAudio(projectName, name);
      await onReload?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeletingAudio(false);
    }
  };

  const toggleAudioPlayback = () => {
    const el = audioElRef.current;
    if (!el) return;
    if (isAudioPlaying) {
      el.pause();
    } else {
      void el.play();
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(name, {
        description,
        voiceStyle,
        referenceFile,
        audioFile,
      });
    } finally {
      setSaving(false);
    }
  };

  const localSheetUrl = character.character_sheet
    ? API.getFileUrl(projectName, character.character_sheet, sheetFp)
    : null;
  const globalImageUrl = linkedAsset?.image_path
    ? API.getGlobalAssetUrl(linkedAsset.image_path, linkedAsset.updated_at)
    : null;
  const hasGlobalLink = Boolean(character.global_asset_id || character.matched_global_asset_id);
  const usingGlobalMain = hasGlobalLink && (character.global_asset_image_usage ?? "main") === "main";
  const sheetUrl = usingGlobalMain && globalImageUrl
    ? globalImageUrl
    : localSheetUrl;

  const savedReferenceUrl = character.reference_image
    ? API.getFileUrl(projectName, character.reference_image, referenceFp)
    : null;

  const linkedReferenceUrl = character.global_asset_image_usage === "reference" ? globalImageUrl : null;
  const displayedReferenceUrl = referencePreview ?? linkedReferenceUrl ?? savedReferenceUrl;
  const hasSavedReference = Boolean(savedReferenceUrl) && !referencePreview;

  const savedAudioUrl = character.reference_audio
    ? API.getFileUrl(projectName, character.reference_audio, audioFp)
    : null;

  const linkedAudioUrl = character.global_asset_voice_source === "reference_audio" && linkedAsset?.audio_path
    ? API.getGlobalAssetUrl(linkedAsset.audio_path, linkedAsset.updated_at)
    : null;
  const usingLinkedVoiceId = character.global_asset_voice_source === "voice_id" && Boolean(linkedAsset?.voice_id);
  const displayedAudioUrl = usingLinkedVoiceId ? null : audioPreview ?? linkedAudioUrl ?? savedAudioUrl;
  const usingLinkedAudio = Boolean(linkedAudioUrl) && !audioPreview;

  useEffect(() => {
    // 音源切换（更换/删除/上游变化）时复位播放状态，避免残留上一段的播放进度
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsAudioPlaying(false);
    setAudioProgress(0);
    setAudioDuration(null);
  }, [displayedAudioUrl]);

  return (
    <div
      id={`character-${name}`}
      className="relative overflow-hidden rounded-xl p-5"
      data-workspace-editing={isEditing || isDirty ? "true" : undefined}
      onFocusCapture={() => setIsEditing(true)}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }
        setIsEditing(false);
      }}
      style={{
        background:
          "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))",
        border: "1px solid var(--color-hairline-soft)",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 12px 30px -12px oklch(0 0 0 / 0.4)",
      }}
    >
      {/* Top accent hairline */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-5 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      {/* ---- Header: 单排 icon + name + icon-only 工具栏 ---- */}
      <div className="mb-4 flex items-center gap-2.5">
        <span
          aria-hidden
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md"
          style={{
            background: "var(--color-accent-dim)",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
          }}
        >
          <User className="h-3.5 w-3.5" />
        </span>
        <EditableAssetName
          projectName={projectName}
          name={name}
          assetType="character"
          readOnly={readOnly}
          /* 改名会搬动落盘文件：与本卡片自身在途的写请求交错会留下旧名孤儿文件，
             因此改名比兄弟控件多禁用一档，把卡片本地的在途标志也算进占用态。 */
          busy={generating || uploadingSheet || saving || deletingAudio}
        />
        {readOnly ? null : (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet || generating}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <Upload className="h-3.5 w-3.5" />
          </button>
          <input
            ref={sheetInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:upload_sheet")}
            className="hidden"
            onChange={(e) => void handleSheetUpload(e)}
          />
          <ImageEditButton
            projectName={projectName}
            resourceType="character"
            resourceId={name}
            hasImage={Boolean(character.character_sheet) && !usingGlobalMain}
            busy={generating || uploadingSheet}
          />
          <AddToLibraryButton
            resourceType="character"
            resourceId={name}
            projectName={projectName}
            initialDescription={character.description}
            initialVoiceStyle={character.voice_style ?? ""}
            sheetPath={character.character_sheet}
            busy={generating || uploadingSheet}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="characters"
            resourceId={name}
            onRestore={onRestoreVersion}
            iconOnly
            busy={generating || uploadingSheet}
          />
          <ProjectAssetDeleteButton
            projectName={projectName}
            assetType="character"
            name={name}
            busy={generating || uploadingSheet || saving || deletingAudio}
            onReload={onReload}
          />
        </div>
        )}
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4 space-y-3">
        <div>
          <div className="flex items-center justify-between">
            <CapsLabel>{t("character_design")}</CapsLabel>
            {readOnly ? null : <ProjectAssetLinkControl
              projectName={projectName}
              resourceType="character"
              resourceId={name}
              matchedAssetId={character.matched_global_asset_id}
              linkedAssetId={character.global_asset_id}
              imageUsage={character.global_asset_image_usage}
              voiceSource={character.global_asset_voice_source}
              showImageUsageControl={false}
              showVoiceSourceControl={false}
              onAssetResolved={handleLinkedAssetResolved}
              onReload={onReload}
              busy={generating || uploadingSheet || saving || deletingAudio}
            />}
          </div>
          <div
            className="mt-1.5 overflow-hidden rounded-lg"
            style={{ border: "1px solid var(--color-hairline-soft)" }}
          >
            <PreviewableImageFrame
              src={sheetUrl && !imgError ? sheetUrl : null}
              alt={`${name} ${t("character_design")}`}
            >
              <AspectFrame ratio="16:9">
                <ImageFlipReveal
                  src={sheetUrl && !imgError ? sheetUrl : null}
                  alt={`${name} ${t("character_design")}`}
                  className="h-full w-full object-contain"
                  onError={() => setImgError(true)}
                  fallback={
                    <div
                      className="flex h-full w-full flex-col items-center justify-center gap-2"
                      style={{ color: "var(--color-text-4)" }}
                    >
                      <User className="h-10 w-10" />
                      <span className="text-xs">{t("click_to_generate")}</span>
                    </div>
                  }
                />
              </AspectFrame>
            </PreviewableImageFrame>
          </div>
        </div>

        {!readOnly && hasGlobalLink && globalImageUrl ? (
          <div className="-my-1 flex justify-center">
            <ProjectAssetImageUsageSwitch
              projectName={projectName}
              resourceType="character"
              resourceId={name}
              imageUsage={character.global_asset_image_usage}
              onReload={onReload}
              busy={generating || uploadingSheet || saving || deletingAudio}
            />
          </div>
        ) : null}

        {/* 只读且没有参考图时整块不渲染：留一个不能用的上传框只是噪声 */}
        {readOnly && !displayedReferenceUrl ? null : (
        <div>
          <div className="flex items-center justify-between">
            <CapsLabel>{t("reference_image")}</CapsLabel>
            {!readOnly && (referenceFile || hasSavedReference) && (
              <button
                type="button"
                onClick={() =>
                  referenceFile
                    ? clearPendingReference()
                    : fileInputRef.current?.click()
                }
                className="focus-ring text-[11px] text-[var(--color-text-3)] transition-colors hover:text-[var(--color-text)]"
              >
                {referenceFile ? t("cancel_pending") : t("replace")}
              </button>
            )}
          </div>

          {displayedReferenceUrl ? (
            <PreviewableImageFrame
              src={displayedReferenceUrl}
              alt={`${name} ${t("reference_image")}`}
              buttonClassName="right-2.5 top-2.5"
            >
              <div
                className="relative mt-1.5 overflow-hidden rounded-lg"
                style={{ border: "1px solid var(--color-hairline-soft)" }}
              >
                <img
                  src={displayedReferenceUrl}
                  alt={`${name} ${t("reference_image")}`}
                  className="h-28 w-full object-cover"
                />
                <div
                  className="absolute inset-x-0 bottom-0 flex items-center justify-between px-3 py-2"
                  style={{
                    background:
                      "linear-gradient(180deg, transparent, oklch(0 0 0 / 0.65))",
                  }}
                >
                  <span
                    className="flex items-center gap-1.5 text-[11px]"
                    style={{ color: "var(--color-text)" }}
                  >
                    <ImagePlus className="h-3.5 w-3.5" />
                    {referenceFile ? t("unsaved_reference") : t("saved_reference")}
                  </span>
                  {readOnly ? null : (
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="focus-ring rounded px-2 py-0.5 text-[11px] transition-colors"
                    style={{
                      background: "oklch(0 0 0 / 0.5)",
                      color: "var(--color-text)",
                      border: "1px solid oklch(1 0 0 / 0.1)",
                    }}
                  >
                    {t("change")}
                  </button>
                  )}
                </div>
              </div>
            </PreviewableImageFrame>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-4 text-sm text-[var(--color-text-4)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text-2)]"
              style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
            >
              <Upload className="h-4 w-4" />
              {t("upload_reference")}
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("upload_character_ref_aria")}
            onChange={handleReferenceChange}
            className="hidden"
          />
        </div>
        )}
      </div>

      <CapsLabel htmlFor={descId}>{t("description")}</CapsLabel>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        readOnly={readOnly}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={3}
        className="focus-ring mt-1.5 w-full resize-none overflow-hidden rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
        placeholder={t("character_desc_placeholder")}
      />

      <div className="mt-3">
        {/* 「声音」是描述输入 + 音频样本共用的分组标题，不单独绑定输入框；
            输入框自带 aria-label 保留「声音风格」这一字段身份 */}
        <div className="flex items-center justify-between gap-2">
          <CapsLabel>{t("voice_section")}</CapsLabel>
          {readOnly ? null : (
            <ProjectAssetVoiceSourceSwitch
              projectName={projectName}
              resourceType="character"
              resourceId={name}
              asset={linkedAsset}
              voiceSource={character.global_asset_voice_source}
              onReload={onReload}
              busy={generating || uploadingSheet || saving || deletingAudio || Boolean(audioFile)}
            />
          )}
        </div>
        <input
          id={voiceStyleId}
          type="text"
          value={voiceStyle}
          readOnly={readOnly}
          aria-label={t("voice_style")}
          onChange={(e) => setVoiceStyle(e.target.value)}
          className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none transition-[border-color,box-shadow]"
          style={FIELD_STYLE}
          placeholder={t("voice_style_example")}
        />

        {character.voice_id && !usingLinkedVoiceId && (
          <div
            className="mt-1.5 flex items-start gap-2 rounded-lg px-2.5 py-2"
            style={FIELD_STYLE}
          >
            <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-text-4">
              {t("assets:field.voice_id")}
            </span>
            <code className="min-w-0 flex-1 break-all text-right text-[10.5px] text-text-3">
              {character.voice_id}
            </code>
          </div>
        )}

        {usingLinkedVoiceId ? (
          <div
            className="mt-1.5 flex items-center gap-2 rounded-lg px-2.5 py-2"
            style={FIELD_STYLE}
          >
            <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-text-4">
              {t("assets:field.voice_id")}
            </span>
            <code className="min-w-0 flex-1 break-all text-right text-[10.5px] text-text-3">
              {linkedAsset?.voice_id}
            </code>
          </div>
        ) : readOnly && !displayedAudioUrl ? null : (
        <div className="mt-1.5">
          {displayedAudioUrl ? (
            <div
              className="flex items-center gap-2 rounded-lg px-2.5 py-1.5"
              style={FIELD_STYLE}
            >
              <button
                type="button"
                onClick={toggleAudioPlayback}
                aria-label={isAudioPlaying ? t("pause_audio_sample") : t("play_audio_sample")}
                className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-full transition-colors"
                style={{
                  background: "var(--color-accent-dim)",
                  border: "1px solid var(--color-accent-soft)",
                  color: "var(--color-accent-2)",
                }}
              >
                {isAudioPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 translate-x-px" />}
              </button>

              <div
                className="relative h-1 flex-1 overflow-hidden rounded-full"
                style={{ background: "var(--color-hairline)" }}
              >
                <div
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    width: `${Math.round(audioProgress * 100)}%`,
                    background:
                      "linear-gradient(90deg, var(--color-accent-2), var(--color-accent))",
                  }}
                />
              </div>

              <span className="shrink-0 text-[10px] tabular-nums" style={{ color: "var(--color-text-4)" }}>
                {formatAudioDuration(audioDuration)}
              </span>

              <span className="shrink-0 text-[11px]" style={{ color: "var(--color-text-3)" }}>
                {audioFile ? t("unsaved_audio") : t("saved_audio")}
              </span>

              {readOnly || usingLinkedAudio ? null : (
              <div className="flex shrink-0 items-center gap-0.5">
                <VoiceSampleButton
                  projectName={projectName}
                  characterName={name}
                  initialVoiceId={character.global_asset_voice_source === "voice_id" ? linkedAsset?.voice_id ?? undefined : undefined}
                  busy={generating || deletingAudio || Boolean(audioFile)}
                  onSaved={() => onReload?.()}
                />
                <button
                  type="button"
                  onClick={() => audioInputRef.current?.click()}
                  title={t("change")}
                  aria-label={t("upload_character_audio_ref_aria")}
                  className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
                  style={{ color: "var(--color-text-3)" }}
                >
                  <Upload className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteAudio()}
                  disabled={deletingAudio}
                  title={audioFile ? t("cancel_pending") : t("delete_audio_sample")}
                  aria-label={audioFile ? t("cancel_pending") : t("delete_audio_sample")}
                  className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ color: "var(--color-text-3)" }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              )}

              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 角色参考音频样本，无对白内容，无字幕源 */}
              <audio
                ref={audioElRef}
                src={displayedAudioUrl}
                preload="metadata"
                className="hidden"
                onPlay={() => setIsAudioPlaying(true)}
                onPause={() => setIsAudioPlaying(false)}
                onLoadedMetadata={(e) => setAudioDuration(e.currentTarget.duration)}
                onTimeUpdate={(e) => {
                  const el = e.currentTarget;
                  setAudioProgress(el.duration ? el.currentTime / el.duration : 0);
                }}
                onEnded={(e) => {
                  e.currentTarget.currentTime = 0;
                  setIsAudioPlaying(false);
                  setAudioProgress(0);
                }}
              />
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => audioInputRef.current?.click()}
                className="focus-ring flex flex-1 items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-2.5 text-[13px] text-[var(--color-text-4)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text-2)]"
                style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
              >
                <Upload className="h-3.5 w-3.5" />
                {t("upload_reference_audio")}
              </button>
              {readOnly ? null : (
                <VoiceSampleButton
                  projectName={projectName}
                  characterName={name}
                  initialVoiceId={character.global_asset_voice_source === "voice_id" ? linkedAsset?.voice_id ?? undefined : undefined}
                  busy={generating || deletingAudio || Boolean(audioFile)}
                  onSaved={() => onReload?.()}
                />
              )}
            </div>
          )}
          {!displayedAudioUrl && (
            <p className="mt-1 text-[10px]" style={{ color: "var(--color-text-4)" }}>
              {t("reference_audio_hint")}
            </p>
          )}
          <input
            ref={audioInputRef}
            type="file"
            accept=".wav,.mp3"
            aria-label={t("upload_character_audio_ref_aria")}
            onChange={handleAudioChange}
            className="hidden"
          />
        </div>
        )}
      </div>

      {isDirty && !readOnly && (
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          {saving ? t("common:saving") : t("common:save")}
        </button>
      )}

      {readOnly ? null : (
      <div className="mt-4">
        <ImageModelSelect value={imageModel} onChange={setImageModel} capability="any" />
        <GenerateButton
          onClick={() => imageModel
            ? onGenerate(name, imageSelectionFromValue(imageModel))
            : onGenerate(name)}
          loading={generating}
          disabled={usingGlobalMain}
          label={character.character_sheet ? t("regenerate_design") : t("generate_design")}
          className="mt-2 w-full justify-center"
        />
      </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

function CapsLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: "var(--color-text-4)" }}
    >
      {children}
    </label>
  );
}
