import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useProjectsStore } from "@/stores/projects-store";
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
  onAddToLibrary?: () => void;
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
  generating = false,
}: PropCardProps) {
  const { t } = useTranslation("dashboard");
  const sheetFp = useProjectsStore(
    (s) => prop.prop_sheet ? s.getAssetFingerprint(prop.prop_sheet) : null,
  );
  const [description, setDescription] = useState(prop.description);
  const [imgError, setImgError] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  const isDirty = description !== prop.description;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 可编辑描述字段必须跟随外部 prop 更新，拷贝模式是有意设计
    setDescription(prop.description);
  }, [prop.description]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 图片源变更时重置错误态，确保新 URL 正常加载
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
      {/* ---- Header: name only ---- */}
      <div className="mb-4 flex items-center gap-2">
        <h3 className="flex-1 text-lg font-bold text-white truncate">{name}</h3>
        <AddToLibraryButton
          resourceType="prop"
          resourceId={name}
          projectName={projectName}
          initialDescription={prop.description}
        />
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
            {t("prop_design")}
          </span>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="props"
            resourceId={name}
            onRestore={onRestoreVersion}
          />
        </div>
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
