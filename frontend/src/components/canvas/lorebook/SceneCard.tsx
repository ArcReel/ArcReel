import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { Landmark, Upload } from "lucide-react";
import { API } from "@/api";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/canvas/shared/AspectFrame";
import { GenerateButton } from "@/components/canvas/lorebook/GenerateButton";
import { PreviewableImageFrame } from "@/components/canvas/shared/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { EditableAssetName } from "./EditableAssetName";
import { MergeAssetMenu } from "./MergeAssetMenu";
import { AssetAliasesField } from "./AssetAliasesField";
import { AssetSheetStaleBadge, MissingDescriptionChip, hasUsableDescription, sheetIsPending } from "./AssetSheetStatusBadge";
import { useStaleRegenerateConfirm } from "./useStaleRegenerateConfirm";
import type { AssetSheetStatusRow, Scene } from "@/types";

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
  onReload?: () => void | Promise<unknown>;
  generating?: boolean;
  /** 产物清单对这张资产图的判定；未取到时按项目数据展示。 */
  sheetStatus?: AssetSheetStatusRow;
  /** 只读展示（引导演示项目）：不渲染上传 / 编辑 / 入库 / 版本 / 生成入口，文本字段只读。 */
  readOnly?: boolean;
}

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--border)",
  color: "var(--foreground)",
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
  sheetStatus,
  readOnly = false,
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
    if (rejectIfAssetBusy("scene", projectName, name, t)) return;
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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 上游场景描述变化时同步本地草稿
    setDescription(scene.description);
  }, [scene.description]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 场景立绘变化时重置图片加载错误标记
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

  const sheetUrl = scene.scene_sheet && !sheetIsPending(sheetStatus)
    ? API.getFileUrl(projectName, scene.scene_sheet, sheetFp)
    : null;
  const descriptionMissing = !hasUsableDescription(scene.description);
  const staleConfirm = useStaleRegenerateConfirm({
    projectName,
    assetType: "scene",
    name,
    status: sheetStatus,
    hasSheet: Boolean(scene.scene_sheet),
    onGenerate: () => onGenerate(name),
  });

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
        border: "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 12px 30px -12px oklch(0 0 0 / 0.4)",
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-5 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, color-mix(in oklab, var(--primary) 22%, transparent), transparent)",
        }}
      />

      {/* ---- Header: 单排 icon + name + icon-only 工具栏 ---- */}
      <div className="mb-4 flex items-center gap-2.5">
        <span
          aria-hidden
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md"
          style={{
            background: "color-mix(in oklab, var(--primary) 12%, transparent)",
            border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
            color: "var(--primary)",
          }}
        >
          <Landmark className="h-3.5 w-3.5" />
        </span>
        <EditableAssetName
          projectName={projectName}
          name={name}
          assetType="scene"
          readOnly={readOnly}
          busy={generating || uploadingSheet}
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
            style={{ color: "var(--muted-foreground)" }}
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
            resourceType="scene"
            resourceId={name}
            hasImage={Boolean(scene.scene_sheet)}
            busy={generating || uploadingSheet}
          />
          <AddToLibraryButton
            resourceType="scene"
            resourceId={name}
            projectName={projectName}
            initialDescription={scene.description}
            sheetPath={scene.scene_sheet}
            busy={generating || uploadingSheet}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="scenes"
            resourceId={name}
            onRestore={onRestoreVersion}
            iconOnly
            busy={generating || uploadingSheet}
          />
          <MergeAssetMenu
            projectName={projectName}
            assetType="scene"
            name={name}
            description={scene.description}
            busy={generating || uploadingSheet}
          />
        </div>
        )}
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <CapsLabel>{t("scene_design")}</CapsLabel>
        <div
          className="relative mt-1.5 overflow-hidden rounded-lg"
          style={{ border: "1px solid color-mix(in oklab, var(--border) 50%, transparent)" }}
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
                  style={{ color: "var(--muted-foreground)" }}
                >
                  <Landmark className="h-10 w-10" />
                  <span className="text-xs">{t("click_to_generate")}</span>
                </div>
              )}
            </AspectFrame>
          </PreviewableImageFrame>
          {sheetUrl && !imgError && <AssetSheetStaleBadge status={sheetStatus} />}
        </div>
      </div>

      {/* ---- Description ---- */}
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          <CapsLabel htmlFor={descId}>{t("description")}</CapsLabel>
          {descriptionMissing && <MissingDescriptionChip />}
        </span>
        {readOnly ? null : (
          <PromptPreviewButton
            title={t("assets:prompt_preview_title", { name })}
            notice={t("assets:prompt_preview_draft")}
            load={(signal) => API.previewAssetPrompt(projectName, "scene", name, description, { signal })}
          />
        )}
      </div>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        readOnly={readOnly}
        rows={2}
        className="focus-ring mt-1.5 mb-3 w-full resize-none overflow-hidden rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
        placeholder={t("scene_desc_placeholder")}
      />

      <AssetAliasesField
        projectName={projectName}
        name={name}
        assetType="scene"
        aliases={scene.aliases ?? []}
        readOnly={readOnly}
      />

      {isDirty && !readOnly && (
        <button
          type="button"
          onClick={handleSave}
          className="focus-ring mb-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "var(--primary)",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px color-mix(in oklab, var(--primary) 35%, transparent), 0 0 0 1px color-mix(in oklab, var(--primary) 22%, transparent)",
          }}
        >
          {t("common:save")}
        </button>
      )}

      {readOnly ? null : (
        <span className="block" title={descriptionMissing ? t("assets:sheet_description_required") : undefined}>
          <GenerateButton
            onClick={staleConfirm.request}
            loading={generating}
            disabled={descriptionMissing}
            label={scene.scene_sheet ? t("regenerate_design") : t("generate_design")}
            className="w-full justify-center"
          />
        </span>
      )}
      {staleConfirm.dialog}
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
      style={{ color: "var(--muted-foreground)" }}
    >
      {children}
    </label>
  );
}
