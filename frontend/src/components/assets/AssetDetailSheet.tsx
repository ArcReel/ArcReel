import { useCallback, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, FolderInput, ImagePlus, Loader2, Pencil, Trash2 } from "lucide-react";
import { API } from "@/api";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { formatDate } from "@/utils/date-format";
import type { Asset } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";

export type AssetSheetMode = "view" | "edit";

const IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp";
const UPDATED_AT_OPTS: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" };

/**
 * 资产库的详情 Sheet：先展示大图与全部信息，点「编辑」才进入表单。
 * 编辑时名称、描述与声音风格是一个编辑单元，统一保存；更换图片立即执行。
 * 有未保存修改时，关闭 Sheet 或返回详情会弹出离开拦截。
 */
export function AssetDetailSheet({
  asset,
  mode,
  onModeChange,
  onClose,
  onApply,
  onDelete,
  onChanged,
}: {
  /** 为 null 时关闭；关闭动画期间仍显示上一个资产。 */
  asset: Asset | null;
  mode: AssetSheetMode;
  onModeChange: (mode: AssetSheetMode) => void;
  onClose: () => void;
  onApply: (asset: Asset) => void;
  onDelete: (asset: Asset) => void;
  /** 保存或更换图片后的资产。 */
  onChanged: (asset: Asset) => void;
}) {
  const confirmLeave = useConfirmLeave();
  const [shown, setShown] = useState(asset);
  if (asset !== null && asset !== shown) setShown(asset);

  return (
    <Sheet
      open={asset !== null}
      onOpenChange={(open) => {
        if (!open) confirmLeave(onClose);
      }}
    >
      <SheetContent side="right" className="data-[side=right]:w-140">
        {shown &&
          (mode === "edit" ? (
            <AssetEditForm
              key={shown.id}
              asset={shown}
              onBack={() => confirmLeave(() => onModeChange("view"))}
              onChanged={onChanged}
            />
          ) : (
            <AssetDetails
              asset={shown}
              onEdit={() => onModeChange("edit")}
              onApply={() => onApply(shown)}
              onDelete={() => onDelete(shown)}
            />
          ))}
      </SheetContent>
    </Sheet>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function AssetDetails({
  asset,
  onEdit,
  onApply,
  onDelete,
}: {
  asset: Asset;
  onEdit: () => void;
  onApply: () => void;
  onDelete: () => void;
}) {
  const { t, i18n } = useTranslation("assets");
  const Icon = ASSET_TYPE_ICON[asset.type];
  const isCharacter = asset.type === "character";
  const audioUrl = API.getGlobalAssetUrl(asset.audio_path, asset.updated_at);

  return (
    <>
      <SheetHeader>
        <SheetTitle>
          <TruncatedText text={asset.name} />
        </SheetTitle>
        <SheetDescription>{t(`type.${asset.type}`)}</SheetDescription>
      </SheetHeader>
      {/* 只读详情里可能没有可聚焦的元素，正文自身可聚焦，键盘用户才能滚动它 */}
      <SheetBody tabIndex={0} role="region" aria-label={t("details_label", { name: asset.name })}>
        <div className="flex flex-col gap-5">
          <AssetThumb
            imageUrl={API.getGlobalAssetUrl(asset.image_path, asset.updated_at)}
            alt={t("asset_image_alt", { name: asset.name })}
            fallback={<Icon aria-hidden className="size-10" />}
            className="rounded-lg border border-border"
          />
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">{t("source_project")}</dt>
            <dd className="break-words">{asset.source_project ?? t("source_project_none")}</dd>
            <dt className="text-muted-foreground">{t("updated_at")}</dt>
            <dd className="tabular-nums">{formatDate(asset.updated_at, i18n.language, UPDATED_AT_OPTS)}</dd>
          </dl>
          <Section title={t("field.description")}>
            <p className="max-w-[40em] text-sm whitespace-pre-wrap break-words">
              {asset.description || <span className="text-muted-foreground">{t("no_description")}</span>}
            </p>
          </Section>
          {isCharacter && (
            <Section title={t("voice")}>
              <p className="text-sm break-words">
                {asset.voice_style || <span className="text-muted-foreground">{t("voice_style_none")}</span>}
              </p>
              {audioUrl ? (
                // eslint-disable-next-line jsx-a11y/media-has-caption -- 参考音频是音色样本，没有字幕源
                <audio controls preload="none" src={audioUrl} aria-label={t("reference_audio")} className="w-full" />
              ) : (
                <p className="text-sm text-muted-foreground">{t("reference_audio_none")}</p>
              )}
            </Section>
          )}
          {asset.derivatives.length > 0 && (
            <Section title={t("derivatives_with_count", { n: asset.derivatives.length })}>
              <ul className="grid grid-cols-2 gap-3">
                {asset.derivatives.map((derivative) => (
                  <li key={derivative.name} className="flex min-w-0 flex-col gap-1">
                    <AssetThumb
                      imageUrl={API.getGlobalAssetUrl(derivative.image_path, asset.updated_at)}
                      alt={t("library_derivative_thumb", { name: derivative.name })}
                      fallback={<span className="px-2 text-center text-xs">{t("derivative_no_sheet")}</span>}
                      className="rounded-md border border-border"
                    />
                    <TruncatedText text={derivative.name} className="text-sm font-medium" />
                    {derivative.description && (
                      <span className="line-clamp-3 text-xs text-muted-foreground">{derivative.description}</span>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </SheetBody>
      <SheetFooter>
        <Button variant="destructive" className="mr-auto" onClick={onDelete}>
          <Trash2 aria-hidden data-icon="inline-start" />
          {t("delete")}
        </Button>
        <Button variant="outline" onClick={onApply}>
          <FolderInput aria-hidden data-icon="inline-start" />
          {t("apply_to_project")}
        </Button>
        <Button onClick={onEdit}>
          <Pencil aria-hidden data-icon="inline-start" />
          {t("edit")}
        </Button>
      </SheetFooter>
    </>
  );
}

interface AssetFields {
  name: string;
  description: string;
  voice_style: string;
}

function AssetEditForm({
  asset,
  onBack,
  onChanged,
}: {
  asset: Asset;
  onBack: () => void;
  onChanged: (asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const nameId = useId();
  const descriptionId = useId();
  const voiceId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const Icon = ASSET_TYPE_ICON[asset.type];
  const isCharacter = asset.type === "character";

  const source = useMemo<AssetFields>(
    () => ({ name: asset.name, description: asset.description, voice_style: asset.voice_style }),
    [asset.name, asset.description, asset.voice_style],
  );
  const save = useCallback(
    async (value: AssetFields): Promise<AssetFields> => {
      const name = value.name.trim();
      if (!name) throw new Error(t("name_required"));
      const { asset: saved } = await API.updateAsset(asset.id, { ...value, name });
      onChanged(saved);
      return { name: saved.name, description: saved.description, voice_style: saved.voice_style };
    },
    [asset.id, onChanged, t],
  );
  const unit = useEditUnit({ source, save, leaveTitle: t("edit_leave_title", { name: asset.name }) });

  useLeaveGuard({ dirty: false, saving: uploading, save: unit.save });

  const replaceImage = async (file: File) => {
    if (uploading || unit.status === "saving") return;
    setUploading(true);
    try {
      const { asset: updated } = await API.replaceAssetImage(asset.id, file);
      onChanged(updated);
    } catch (err) {
      useAppStore.getState().pushToast(t("replace_image_failed", { message: errMsg(err) }), "error");
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <SheetHeader>
        <div className="flex min-w-0 items-center gap-1">
          <Button variant="ghost" size="icon-sm" className="-ml-2" aria-label={t("back_to_details")} onClick={onBack}>
            <ChevronLeft aria-hidden />
          </Button>
          <SheetTitle className="min-w-0">
            <TruncatedText text={t("edit_title", { type: t(`type.${asset.type}`), name: asset.name })} />
          </SheetTitle>
        </div>
      </SheetHeader>
      <SheetBody>
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <AssetThumb
              imageUrl={API.getGlobalAssetUrl(asset.image_path, asset.updated_at)}
              alt={t("asset_image_alt", { name: asset.name })}
              fallback={<Icon aria-hidden className="size-10" />}
              className="rounded-lg border border-border"
            />
            <div>
              <Button
                variant="outline"
                size="sm"
                disabled={uploading || unit.status === "saving"}
                onClick={() => fileRef.current?.click()}
              >
                {uploading ? (
                  <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                ) : (
                  <ImagePlus aria-hidden data-icon="inline-start" />
                )}
                {t(asset.image_path ? "replace_image_action" : "upload_image_action")}
              </Button>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept={IMAGE_ACCEPT}
              hidden
              disabled={uploading || unit.status === "saving"}
              aria-label={t("upload_image_action")}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void replaceImage(file);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>{t("field.name")}</Label>
            <Input
              id={nameId}
              value={unit.value.name}
              onChange={(event) => {
                const name = event.target.value;
                unit.setValue((prev) => ({ ...prev, name }));
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={descriptionId}>{t("field.description")}</Label>
            <Textarea
              id={descriptionId}
              value={unit.value.description}
              onChange={(event) => {
                const description = event.target.value;
                unit.setValue((prev) => ({ ...prev, description }));
              }}
            />
          </div>
          {isCharacter && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={voiceId}>{t("field.voice_style")}</Label>
              <Input
                id={voiceId}
                value={unit.value.voice_style}
                onChange={(event) => {
                  const voiceStyle = event.target.value;
                  unit.setValue((prev) => ({ ...prev, voice_style: voiceStyle }));
                }}
              />
            </div>
          )}
          {asset.derivatives.length > 0 && (
            <p className="text-sm text-muted-foreground">
              {t("library_derivatives_edit_note", { count: asset.derivatives.length })}
            </p>
          )}
        </div>
      </SheetBody>
      <SheetFooter>
        <fieldset disabled={uploading} className="min-w-0 flex-1">
          <SaveBar unit={unit} />
        </fieldset>
      </SheetFooter>
    </>
  );
}
