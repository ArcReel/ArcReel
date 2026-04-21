import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Package, Upload } from "lucide-react";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { Prop } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface PropCardProps {
  name: string;
  prop: Prop;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Prop>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => void | Promise<void>;
  onReload?: () => void | Promise<void>;
  generating?: boolean;
}

// ---------------------------------------------------------------------------
// PropCard
// ---------------------------------------------------------------------------

export function PropCard({
  name,
  prop,
  projectName,
  onUpdate,
  onGenerate,
  onRestoreVersion,
  onReload,
  generating = false,
}: PropCardProps) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const sheetFp = useProjectsStore(
    (s) => prop.prop_sheet ? s.getAssetFingerprint(prop.prop_sheet) : null,
  );
  const [description, setDescription] = useState(prop.description);
  const [imgError, setImgError] = useState(false);
  const [uploadingSheet, setUploadingSheet] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const sheetInputRef = useRef<HTMLInputElement>(null);

  const handleSheetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingSheet(true);
    try {
      await API.uploadFile(projectName, "prop", file, name);
      await onReload?.();
      useAppStore.getState().pushToast(t("assets:upload_sheet_success", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingSheet(false);
    }
  };

  const isDirty = description !== prop.description;

  useEffect(() => {
    setDescription(prop.description);
  }, [prop.description]);

  useEffect(() => {
    setImgError(false);
  }, [prop.prop_sheet, sheetFp]);

  // Auto-resize textarea.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    autoResize();
  }, [description, autoResize]);

  const handleSave = () => {
    onUpdate(name, { description });
  };

  const sheetUrl = prop.prop_sheet
    ? API.getFileUrl(projectName, prop.prop_sheet, sheetFp)
    : null;

  return (
    <div
      className="bg-gray-900 border border-gray-800 rounded-xl p-5"
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
      {/* ---- Header: name + actions ---- */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="min-w-0 flex-1 truncate text-lg font-bold text-white">{name}</h3>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 disabled:opacity-40"
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
            resourceType="prop"
            resourceId={name}
            projectName={projectName}
            initialDescription={prop.description}
            sheetPath={prop.prop_sheet}
            showLabel
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="props"
            resourceId={name}
            onRestore={onRestoreVersion}
          />
        </div>
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <PreviewableImageFrame
          src={sheetUrl && !imgError ? sheetUrl : null}
          alt={`${name} ${t("prop_design")}`}
        >
          <AspectFrame ratio="16:9">
            {sheetUrl && !imgError ? (
              <img
                src={sheetUrl}
                alt={`${name} ${t("prop_design")}`}
                className="h-full w-full object-cover"
                onError={() => setImgError(true)}
              />
            ) : (
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-500">
                <Package className="h-10 w-10" />
                <span className="text-xs">{t("click_to_generate")}</span>
              </div>
            )}
          </AspectFrame>
        </PreviewableImageFrame>
      </div>

      {/* ---- Description ---- */}
      <label htmlFor={descId} className="text-xs font-medium text-gray-400">{t("description")}</label>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={2}
        className="mb-3 w-full resize-none overflow-hidden bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus-ring"
        placeholder={t("prop_desc_placeholder")}
      />

      {isDirty && (
        <button
          type="button"
          onClick={handleSave}
          className="mb-3 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
        >
          {t("common:save")}
        </button>
      )}

      <GenerateButton
        onClick={() => onGenerate(name)}
        loading={generating}
        label={prop.prop_sheet ? t("regenerate_design") : t("generate_design")}
        className="w-full justify-center"
      />
    </div>
  );
}
