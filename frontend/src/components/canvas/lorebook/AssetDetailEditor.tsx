import { useCallback, useId, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { API } from "@/api";
import { CrossfadeImage } from "@/components/canvas/shared/CrossfadeImage";
import { useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SheetBody, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useProjectsStore } from "@/stores/projects-store";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types";
import type { AssetSheetStatusRow, AssetSheetType, Character, Product } from "@/types";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { ASSET_TYPE_ICON } from "./AssetBrowseCard";
import { rejectIfAssetBusy, useAssetBusyNames } from "./assetBusyGuard";
import { AssetAliasesField } from "./AssetAliasesField";
import { AssetImageDialog } from "./AssetImageDialog";
import { AssetOriginalsField } from "./AssetOriginalsField";
import { hasUsableDescription } from "./AssetSheetStatusBadge";
import {
  assetFieldsEqual,
  assetFieldsOf,
  assetFieldsPatch,
  normalizeAssetFields,
  updateAssetFields,
  type AssetFields,
} from "./asset-editor-model";
import { CharacterDerivativesField } from "./CharacterDerivativesField";
import { CharacterVoiceField } from "./CharacterVoiceField";
import { EditableAssetName } from "./EditableAssetName";
import { markerOf, toGalleryAsset, type GalleryAssetSource } from "./gallery-model";
import { GalleryStatusMarker } from "./GalleryStatusMarker";
import { GenerateButton } from "./GenerateButton";
import { AssetWriteProvider, useAssetWrites } from "./useAssetWrites";
import { useStaleRegenerateConfirm } from "./useStaleRegenerateConfirm";

const DESCRIPTION_PLACEHOLDER = {
  character: "dashboard:character_desc_placeholder",
  scene: "dashboard:scene_desc_placeholder",
  prop: "dashboard:prop_desc_placeholder",
  product: "dashboard:product_desc_placeholder",
} as const satisfies Record<AssetSheetType, string>;

function EditorSection({
  title,
  htmlFor,
  action,
  children,
}: {
  title: string;
  /** 区块只有一个输入框时，标题即它的标签。 */
  htmlFor?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex min-h-7 items-center justify-between gap-2">
        {htmlFor ? <Label htmlFor={htmlFor}>{title}</Label> : <h3 className="text-sm font-medium">{title}</h3>}
        {action}
      </div>
      {children}
    </section>
  );
}

export interface AssetDetailEditorProps {
  projectName: string;
  assetType: AssetSheetType;
  name: string;
  asset: GalleryAssetSource;
  /** 产物清单对这张资产图的判定；未取到时按条目上是否登记了资产图展示。 */
  sheetStatus: AssetSheetStatusRow | undefined;
  /** 资产图任务占用中。 */
  generating: boolean;
  writing?: boolean;
  readOnly: boolean;
  onGenerate: (name: string) => unknown;
  /** 改名已提交时调用。 */
  onRenamed: (from: string, to: string) => void;
  /** 头部右侧的上一个与下一个。 */
  navigation?: ReactNode;
}

/**
 * 一个资产的详情编辑器，放在详情 Sheet 里。一个资产是一个编辑单元：描述、声音风格、品牌与卖点
 * 进入未保存修改，由 Sheet 底部的提示条统一保存；改名、上传、增删别名立即执行。
 * 四类资产共用这一个编辑器，类型差异只体现在字段区块的开关上。
 */
export function AssetDetailEditor({
  projectName,
  assetType,
  name,
  asset,
  sheetStatus,
  generating,
  writing = false,
  readOnly,
  onGenerate,
  onRenamed,
  navigation,
}: AssetDetailEditorProps) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const descriptionId = useId();
  const brandId = useId();
  const sellingPointsId = useId();
  const [viewingSheet, setViewingSheet] = useState(false);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const voiceBinding = useProjectsStore(
    (s) => s.currentProjectData?.character_voice_binding ?? DEFAULT_CHARACTER_VOICE_BINDING,
  );
  const writes = useAssetWrites();
  const occupied = useAssetBusyNames(assetType, projectName).has(name);

  const source = useMemo(() => assetFieldsOf(assetType, asset), [assetType, asset]);
  const save = useCallback(
    async (value: AssetFields, saved: AssetFields) => {
      await updateAssetFields(projectName, assetType, name, assetFieldsPatch(assetType, value, saved));
      await refreshAfterWrite(projectName, t);
      return normalizeAssetFields(value);
    },
    [projectName, assetType, name, t],
  );
  const unit = useEditUnit<AssetFields>({
    source,
    save,
    isEqual: assetFieldsEqual,
    leaveTitle: t("assets:edit_leave_title", { name }),
  });
  const { value, setValue } = unit;
  // 区块里的上传、删除在途时，离开先等它们落定
  useLeaveGuard({ dirty: false, saving: writes.writing || writing, save: unit.save });

  const saving = unit.status === "saving";
  // 改名会搬动落盘文件，与任何在途写请求交错都会留下旧名孤儿文件，所以把它们都算进占用态
  const busy = generating || occupied || saving || writes.writing || writing;

  const gallery = toGalleryAsset(assetType, name, asset);
  const sheetFp = useProjectsStore((s) => (gallery.sheetPath ? s.getAssetFingerprint(gallery.sheetPath) : null));
  const sheetUrl =
    gallery.sheetPath && sheetStatus?.status !== "missing"
      ? API.getFileUrl(projectName, gallery.sheetPath, sheetFp)
      : null;
  const imageUrl = sheetUrl && sheetUrl !== failedUrl ? sheetUrl : null;
  const Icon = ASSET_TYPE_ICON[assetType];
  const imageAlt = t("assets:asset_image_alt", { name });

  const staleConfirm = useStaleRegenerateConfirm({
    projectName,
    assetType,
    name,
    status: sheetStatus,
    hasSheet: Boolean(gallery.sheetPath),
    onGenerate: () => onGenerate(name),
  });

  const generate = () => {
    if (busy || rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return;
    // 资产图只由描述与原图生成：改了描述，保存后这张图就会过期，服务端此刻的判定还看不到
    const willBeStale = value.description !== unit.savedValue.description;
    void unit.saveAndGenerate(() => {
      if (!rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return onGenerate(name);
    }, {
      confirm: async () => {
        if (!(await staleConfirm.confirm({ willBeStale }))) return false;
        return !rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint");
      },
    });
  };

  const setField = <K extends keyof AssetFields>(key: K, next: AssetFields[K]) =>
    setValue((prev) => ({ ...prev, [key]: next }));

  const generateLabel = unit.dirty
    ? t("common:save_and_generate")
    : gallery.sheetPath
      ? t("dashboard:regenerate_design")
      : t("dashboard:generate_design");
  const describable = hasUsableDescription(value.description);
  const character = assetType === "character" ? (asset as Character) : null;
  const product = assetType === "product" ? (asset as Product) : null;
  const aliases = "aliases" in asset ? (asset.aliases ?? []) : [];
  const originals = character
    ? character.reference_image
      ? [character.reference_image]
      : []
    : (product?.reference_images ?? []);

  return (
    <AssetWriteProvider value={writes.track}>
      <SheetHeader>
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1">
            <EditableAssetName
              projectName={projectName}
              name={name}
              assetType={assetType}
              readOnly={readOnly}
              busy={busy}
              onRenamed={onRenamed}
              renderTitle={(hidden) => (
                <SheetTitle className={cn("min-w-0", hidden && "sr-only")}>
                  <TruncatedText text={name} />
                </SheetTitle>
              )}
            />
          </div>
          {navigation}
        </div>
      </SheetHeader>

      <SheetBody>
        <div className="flex flex-col gap-6">
          <section aria-label={t("assets:sheet_section")} className="flex flex-col gap-3">
            <div className="relative aspect-video overflow-hidden rounded-lg border border-border bg-muted/40">
              {imageUrl ? (
                <button
                  type="button"
                  onClick={() => setViewingSheet(true)}
                  aria-label={t("assets:view_image", { name: imageAlt })}
                  className="focus-ring block size-full"
                >
                  <CrossfadeImage
                    src={imageUrl}
                    alt={imageAlt}
                    className="object-contain"
                    onError={() => setFailedUrl(imageUrl)}
                    fallback={null}
                  />
                </button>
              ) : (
                <div className="flex size-full flex-col items-center justify-center gap-2 text-muted-foreground">
                  <Icon aria-hidden className="size-8" />
                  <span className="text-sm">{t("assets:no_sheet")}</span>
                </div>
              )}
              <GalleryStatusMarker
                marker={markerOf(gallery, sheetStatus, generating)}
                className="absolute top-2 left-2"
              />
            </div>
            {readOnly ? null : (
              <div className="flex flex-col gap-1.5">
                <GenerateButton
                  onClick={generate}
                  loading={generating}
                  disabled={!describable || busy}
                  label={generateLabel}
                  className="w-full"
                />
                {describable ? null : (
                  <p className="text-xs text-muted-foreground">{t("assets:sheet_description_required")}</p>
                )}
              </div>
            )}
          </section>

          {readOnly && originals.length === 0
            ? null
            : (character || product) && (
                <EditorSection title={product ? t("dashboard:product_reference_images") : t("assets:original_section")}>
                  <AssetOriginalsField
                    projectName={projectName}
                    name={name}
                    assetType={product ? "product" : "character"}
                    paths={originals}
                    readOnly={readOnly}
                    busy={busy}
                  />
                </EditorSection>
              )}

          <EditorSection
            title={t("assets:field.description")}
            htmlFor={descriptionId}
            action={
              readOnly ? null : (
                <PromptPreviewButton
                  title={t("assets:prompt_preview_title", { name })}
                  beforeOpen={unit.save}
                  saveFirst={unit.dirty}
                  // 保存在途时再「保存并预览」会并发第二次保存，先后落定可能让旧草稿盖住新草稿
                  disabled={saving}
                  load={(signal) => API.previewAssetPrompt(projectName, assetType, name, value.description, { signal })}
                />
              )
            }
          >
            <Textarea
              id={descriptionId}
              value={value.description}
              readOnly={readOnly}
              onChange={(e) => setField("description", e.target.value)}
              rows={3}
              placeholder={t(DESCRIPTION_PLACEHOLDER[assetType])}
            />
          </EditorSection>

          {product && (
            <>
              <EditorSection title={t("dashboard:product_brand_label")} htmlFor={brandId}>
                <Input
                  id={brandId}
                  value={value.brand}
                  readOnly={readOnly}
                  onChange={(e) => setField("brand", e.target.value)}
                  placeholder={t("dashboard:product_brand_placeholder")}
                />
              </EditorSection>
              <EditorSection title={t("dashboard:product_selling_points_label")} htmlFor={sellingPointsId}>
                <Textarea
                  id={sellingPointsId}
                  value={value.sellingPoints}
                  readOnly={readOnly}
                  onChange={(e) => setField("sellingPoints", e.target.value)}
                  rows={3}
                  placeholder={t("dashboard:product_selling_points_placeholder")}
                />
              </EditorSection>
            </>
          )}

          {character && (
            <EditorSection title={t("dashboard:voice_section")}>
              <CharacterVoiceField
                projectName={projectName}
                name={name}
                voiceStyle={value.voiceStyle}
                onVoiceStyleChange={(next) => setField("voiceStyle", next)}
                referenceAudio={character.reference_audio}
                voiceBinding={voiceBinding}
                readOnly={readOnly}
                busy={busy}
              />
            </EditorSection>
          )}

          {assetType !== "product" && !(readOnly && aliases.length === 0) && (
            <EditorSection title={t("assets:aliases_label")}>
              <AssetAliasesField
                projectName={projectName}
                name={name}
                assetType={assetType}
                aliases={aliases}
                readOnly={readOnly}
                busy={busy}
              />
            </EditorSection>
          )}

          {character && !(readOnly && Object.keys(character.derivatives ?? {}).length === 0) && (
            <EditorSection title={t("assets:derivatives")}>
              <CharacterDerivativesField
                projectName={projectName}
                characterName={name}
                derivatives={character.derivatives ?? {}}
                ownerSheet={character.character_sheet}
                readOnly={readOnly}
                busy={busy}
              />
            </EditorSection>
          )}
        </div>
      </SheetBody>

      {readOnly ? null : <UnsavedChangesBar unit={unit} className="mx-5 mb-4 shrink-0" />}

      <AssetImageDialog
        src={viewingSheet ? imageUrl : null}
        title={name}
        alt={imageAlt}
        onClose={() => setViewingSheet(false)}
      />
      {staleConfirm.dialog}
    </AssetWriteProvider>
  );
}
