import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Landmark, Upload } from "lucide-react";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { Scene } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SceneCardProps {
  name: string;
  scene: Scene;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Scene>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => void | Promise<void>;
  onReload?: () => void | Promise<void>;
  generating?: boolean;
}

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

// ---------------------------------------------------------------------------
// SceneCard
// ---------------------------------------------------------------------------

export function SceneCard({
  name,
  scene,
  projectName,
  onUpdate,
  onGenerate,
  onRestoreVersion,
  onReload,
  generating = false,
}: SceneCardProps) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const sheetFp = useProjectsStore(
    (s) => scene.scene_sheet ? s.getAssetFingerprint(scene.scene_sheet) : null,
  );
  const [description, setDescription] = useState(scene.description);
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
      await API.uploadFile(projectName, "scene", file, name);
      await onReload?.();
      useAppStore.getState().pushToast(t("assets:upload_sheet_success", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingSheet(false);
    }
  };

  const isDirty = description !== scene.description;

  useEffect(() => {
    // 上游场景描述变化时同步本地草稿
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(scene.description);
  }, [scene.description]);

  useEffect(() => {
    // 场景立绘变化时重置图片加载错误标记
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setImgError(false);
  }, [scene.scene_sheet, sheetFp]);

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

  const sheetUrl = scene.scene_sheet
    ? API.getFileUrl(projectName, scene.scene_sheet, sheetFp)
    : null;

  return (
    <div
      id={`scene-${name}`}
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
          <Landmark className="h-3.5 w-3.5" />
        </span>
        <h3
          className="display-serif min-w-0 flex-1 truncate text-[16px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {name}
        </h3>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
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
          <AddToLibraryButton
            resourceType="scene"
            resourceId={name}
            projectName={projectName}
            initialDescription={scene.description}
            sheetPath={scene.scene_sheet}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="scenes"
            resourceId={name}
            onRestore={onRestoreVersion}
            iconOnly
          />
        </div>
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <CapsLabel>{t("scene_design")}</CapsLabel>
        <div
          className="mt-1.5 overflow-hidden rounded-lg"
          style={{ border: "1px solid var(--color-hairline-soft)" }}
        >
          <PreviewableImageFrame
            src={sheetUrl && !imgError ? sheetUrl : null}
            alt={`${name} ${t("scene_design")}`}
          >
            <AspectFrame ratio="16:9">
              {sheetUrl && !imgError ? (
                <img
                  src={sheetUrl}
                  alt={`${name} ${t("scene_design")}`}
                  className="h-full w-full object-cover"
                  onError={() => setImgError(true)}
                />
              ) : (
                <div
                  className="flex h-full w-full flex-col items-center justify-center gap-2"
                  style={{ color: "var(--color-text-4)" }}
                >
                  <Landmark className="h-10 w-10" />
                  <span className="text-xs">{t("click_to_generate")}</span>
                </div>
              )}
            </AspectFrame>
          </PreviewableImageFrame>
        </div>
      </div>

      {/* ---- Description ---- */}
      <CapsLabel htmlFor={descId}>{t("description")}</CapsLabel>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={2}
        className="focus-ring mt-1.5 mb-3 w-full resize-none overflow-hidden rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
        placeholder={t("scene_desc_placeholder")}
      />

      {isDirty && (
        <button
          type="button"
          onClick={handleSave}
          className="focus-ring mb-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          {t("common:save")}
        </button>
      )}

      <GenerateButton
        onClick={() => onGenerate(name)}
        loading={generating}
        label={scene.scene_sheet ? t("regenerate_design") : t("generate_design")}
        className="w-full justify-center"
      />
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
