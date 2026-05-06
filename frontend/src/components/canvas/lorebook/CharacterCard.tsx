import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Upload, User } from "lucide-react";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { Character } from "@/types";

interface CharacterSavePayload {
  description: string;
  voiceStyle: string;
  referenceFile?: File | null;
}

interface CharacterCardProps {
  name: string;
  character: Character;
  projectName: string;
  onSave: (name: string, payload: CharacterSavePayload) => Promise<void>;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<void> | void;
  onReload?: () => Promise<void> | void;
  generating?: boolean;
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
}: CharacterCardProps) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const sheetFp = useProjectsStore(
    (s) => character.character_sheet ? s.getAssetFingerprint(character.character_sheet) : null,
  );
  const referenceFp = useProjectsStore(
    (s) => character.reference_image ? s.getAssetFingerprint(character.reference_image) : null,
  );
  const [description, setDescription] = useState(character.description);
  const [voiceStyle, setVoiceStyle] = useState(character.voice_style ?? "");
  const [imgError, setImgError] = useState(false);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [uploadingSheet, setUploadingSheet] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sheetInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();

  const handleSheetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
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
    setDescription(character.description);
    setVoiceStyle(character.voice_style ?? "");
  }, [character.description, character.voice_style]);

  useEffect(() => {
    setImgError(false);
  }, [character.character_sheet, sheetFp]);

  useEffect(() => {
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
    referenceFile !== null;

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

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(name, {
        description,
        voiceStyle,
        referenceFile,
      });
    } finally {
      setSaving(false);
    }
  };

  const sheetUrl = character.character_sheet
    ? API.getFileUrl(projectName, character.character_sheet, sheetFp)
    : null;

  const savedReferenceUrl = character.reference_image
    ? API.getFileUrl(projectName, character.reference_image, referenceFp)
    : null;

  const displayedReferenceUrl = referencePreview ?? savedReferenceUrl;
  const hasSavedReference = Boolean(savedReferenceUrl) && !referencePreview;

  return (
    <div
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

      {/* ---- Header: 第一排 icon+name；第二排所有动作右对齐 ---- */}
      <div className="mb-4 flex flex-col gap-2">
        <div className="flex items-center gap-2.5">
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
          <h3
            className="display-serif min-w-0 flex-1 truncate text-[16px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {name}
          </h3>
        </div>
        <div className="flex items-center justify-end gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <Upload className="h-3 w-3" />
            <span>{t("assets:upload_sheet_short")}</span>
          </button>
          <input
            ref={sheetInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:upload_sheet")}
            className="hidden"
            onChange={(e) => void handleSheetUpload(e)}
          />
          <AddToLibraryButton
            resourceType="character"
            resourceId={name}
            projectName={projectName}
            initialDescription={character.description}
            initialVoiceStyle={character.voice_style ?? ""}
            sheetPath={character.character_sheet}
            showLabel
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="characters"
            resourceId={name}
            onRestore={onRestoreVersion}
          />
        </div>
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4 space-y-3">
        <div>
          <CapsLabel>{t("character_design")}</CapsLabel>
          <div
            className="mt-1.5 overflow-hidden rounded-lg"
            style={{ border: "1px solid var(--color-hairline-soft)" }}
          >
            <PreviewableImageFrame
              src={sheetUrl && !imgError ? sheetUrl : null}
              alt={`${name} ${t("character_design")}`}
            >
              <AspectFrame ratio="3:4">
                <ImageFlipReveal
                  src={sheetUrl && !imgError ? sheetUrl : null}
                  alt={`${name} ${t("character_design")}`}
                  className="h-full w-full object-cover"
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

        <div>
          <div className="flex items-center justify-between">
            <CapsLabel>{t("reference_image")}</CapsLabel>
            {(referenceFile || hasSavedReference) && (
              <button
                type="button"
                onClick={() =>
                  referenceFile
                    ? clearPendingReference()
                    : fileInputRef.current?.click()
                }
                className="focus-ring text-[11px] transition-colors"
                style={{ color: "var(--color-text-3)" }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--color-text)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--color-text-3)";
                }}
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
                </div>
              </div>
            </PreviewableImageFrame>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg px-3 py-4 text-sm transition-colors"
              style={{
                color: "var(--color-text-4)",
                border: "1px dashed var(--color-hairline)",
                background: "oklch(0.18 0.010 265 / 0.35)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "var(--color-accent-soft)";
                e.currentTarget.style.color = "var(--color-text-2)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "var(--color-hairline)";
                e.currentTarget.style.color = "var(--color-text-4)";
              }}
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
      </div>

      <CapsLabel htmlFor={descId}>{t("description")}</CapsLabel>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={3}
        className="focus-ring mt-1.5 w-full resize-none overflow-hidden rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
        placeholder={t("character_desc_placeholder")}
      />

      <div className="mt-3">
        <CapsLabel>{t("voice_style")}</CapsLabel>
        <input
          type="text"
          value={voiceStyle}
          onChange={(e) => setVoiceStyle(e.target.value)}
          className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none transition-[border-color,box-shadow]"
          style={FIELD_STYLE}
          placeholder={t("voice_style_example")}
        />
      </div>

      {isDirty && (
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

      <div className="mt-4">
        <GenerateButton
          onClick={() => onGenerate(name)}
          loading={generating}
          label={character.character_sheet ? t("regenerate_design") : t("generate_design")}
          className="w-full justify-center"
        />
      </div>
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
