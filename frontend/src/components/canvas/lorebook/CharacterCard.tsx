import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Upload, User } from "lucide-react";
import { API } from "@/api";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useProjectsStore } from "@/stores/projects-store";
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
  generating?: boolean;
}

export function CharacterCard({
  name,
  character,
  projectName,
  onSave,
  onGenerate,
  onRestoreVersion,
  generating = false,
}: CharacterCardProps) {
  const { t } = useTranslation("dashboard");
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
  const [isEditing, setIsEditing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();

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
      className="rounded-xl border border-gray-800 bg-gray-900 p-5"
      data-workspace-editing={isEditing || isDirty ? "true" : undefined}
      onFocusCapture={() => setIsEditing(true)}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }
        setIsEditing(false);
      }}
    >
      <h3 className="mb-4 truncate text-lg font-bold text-white">{name}</h3>

      <div className="mb-4 space-y-3">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
              {t("character_design")}
            </span>
            <VersionTimeMachine
              projectName={projectName}
              resourceType="characters"
              resourceId={name}
              onRestore={onRestoreVersion}
            />
          </div>
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
                  <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-500">
                    <User className="h-10 w-10" />
                    <span className="text-xs">{t("click_to_generate")}</span>
                  </div>
                }
              />
            </AspectFrame>
          </PreviewableImageFrame>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
              {t("reference_image")}
            </span>
            {(referenceFile || hasSavedReference) && (
              <button
                type="button"
                onClick={() =>
                  referenceFile
                    ? clearPendingReference()
                    : fileInputRef.current?.click()
                }
                className="text-xs text-gray-400 transition-colors hover:text-gray-200"
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
              <div className="relative overflow-hidden rounded-lg border border-gray-700 bg-gray-800">
                <img
                  src={displayedReferenceUrl}
                  alt={`${name} ${t("reference_image")}`}
                  className="h-28 w-full object-cover"
                />
                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-3 py-2">
                  <span className="flex items-center gap-1.5 text-xs text-gray-200">
                    <ImagePlus className="h-3.5 w-3.5" />
                    {referenceFile ? t("unsaved_reference") : t("saved_reference")}
                  </span>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="rounded bg-black/40 px-2 py-1 text-xs text-gray-200 transition-colors hover:bg-black/60"
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
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-700 bg-gray-800/50 px-3 py-4 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300"
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

      <label htmlFor={descId} className="text-xs font-medium text-gray-400">{t("description")}</label>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={3}
        className="mt-1 w-full resize-none overflow-hidden rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus-ring"
        placeholder={t("character_desc_placeholder")}
      />

      <label className="mt-3 block text-xs font-medium text-gray-400">{t("voice_style")}</label>
      <input
        type="text"
        value={voiceStyle}
        onChange={(e) => setVoiceStyle(e.target.value)}
        className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus-ring"
        placeholder={t("voice_style_example")}
      />

      {isDirty && (
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="mt-3 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? t("common:saving") : t("common:save")}
        </button>
      )}

      <div className="mt-3">
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
