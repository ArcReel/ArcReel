import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Puzzle } from "lucide-react";
import { API } from "@/api";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useProjectsStore } from "@/stores/projects-store";
import type { Clue } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ClueCardProps {
  name: string;
  clue: Clue;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Clue>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<void> | void;
  generating?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TYPE_LABEL_KEYS: Record<string, string> = {
  prop: "prop_option",
  location: "location_option",
};

// ---------------------------------------------------------------------------
// ClueCard
// ---------------------------------------------------------------------------

export function ClueCard({
  name,
  clue,
  projectName,
  onUpdate,
  onGenerate,
  onRestoreVersion,
  generating = false,
}: ClueCardProps) {
  const { t } = useTranslation("dashboard");
  const sheetFp = useProjectsStore(
    (s) => clue.clue_sheet ? s.getAssetFingerprint(clue.clue_sheet) : null,
  );
  const [description, setDescription] = useState(clue.description);
  const [imgError, setImgError] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  const isDirty = description !== clue.description;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 可编辑描述字段必须跟随外部 prop 更新，拷贝模式是有意设计
    setDescription(clue.description);
  }, [clue.description]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 图片源变更时重置错误态，确保新 URL 正常加载
    setImgError(false);
  }, [clue.clue_sheet, sheetFp]);

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

  const sheetUrl = clue.clue_sheet
    ? API.getFileUrl(projectName, clue.clue_sheet, sheetFp)
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
      {/* ---- Header: name + badges ---- */}
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-lg font-bold text-white truncate">{name}</h3>

        <span className="shrink-0 rounded-full bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-300">
          {t(TYPE_LABEL_KEYS[clue.type] ?? clue.type)}
        </span>

        {clue.importance === "major" ? (
          <span className="shrink-0 rounded-full bg-indigo-500/10 px-2 py-0.5 text-xs font-medium text-indigo-400 border border-indigo-500/20">
            {t("major_option")}
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-400">
            {t("minor_option")}
          </span>
        )}
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
            {t("clue_design")}
          </span>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="clues"
            resourceId={name}
            onRestore={onRestoreVersion}
          />
        </div>
        <PreviewableImageFrame
          src={sheetUrl && !imgError ? sheetUrl : null}
          alt={`${name} ${t("clue_design")}`}
        >
          <AspectFrame ratio="16:9">
            {sheetUrl && !imgError ? (
              <img
                src={sheetUrl}
                alt={`${name} ${t("clue_design")}`}
                className="h-full w-full object-cover"
                onError={() => setImgError(true)}
              />
            ) : (
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-500">
                <Puzzle className="h-10 w-10" />
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
        placeholder={t("clue_desc_placeholder")}
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

      {clue.importance === "major" && (
        <GenerateButton
          onClick={() => onGenerate(name)}
          loading={generating}
          label={clue.clue_sheet ? t("regenerate_design") : t("generate_design")}
          className="w-full justify-center"
        />
      )}
    </div>
  );
}
