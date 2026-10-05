import { useCallback, useEffect, useId } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Loader2 } from "lucide-react";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SheetBody, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { AssetSheetType } from "@/types";
import { normalizeAssetName } from "@/utils/reference-mentions";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { createAsset, EMPTY_ASSET_FIELDS, type NewAssetFields } from "./asset-editor-model";

const BLANK: NewAssetFields = { name: "", ...EMPTY_ASSET_FIELDS };

const DESCRIPTION_PLACEHOLDER = {
  character: "dashboard:character_desc_placeholder",
  scene: "dashboard:scene_desc_placeholder",
  prop: "dashboard:prop_desc_placeholder",
  product: "dashboard:product_desc_placeholder",
} as const satisfies Record<AssetSheetType, string>;

/**
 * 详情 Sheet 的新建模式：空白的详情表单，「创建」即保存。创建后由调用方切到这个资产的详情，
 * 原图、参考音频与别名在那里补充。填了内容再关闭会经离开拦截询问。
 */
export function AssetCreateForm({
  projectName,
  assetType,
  onCancel,
  onCreated,
  onSubmittingChange,
}: {
  projectName: string;
  assetType: AssetSheetType;
  onCancel: () => void;
  /** 创建成功、项目数据已刷新后调用。 */
  onCreated: (name: string) => void;
  /** 创建请求在途时 Sheet 不响应关闭。 */
  onSubmittingChange: (submitting: boolean) => void;
}) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const nameId = useId();
  const descriptionId = useId();
  const voiceStyleId = useId();
  const brandId = useId();
  const sellingPointsId = useId();

  const save = useCallback(
    async (fields: NewAssetFields) => {
      await createAsset(projectName, assetType, fields);
      // 刷新失败时已提示；新资产不在数据里，Sheet 随即关闭，不会停在可重复提交的表单
      await refreshAfterWrite(projectName, t);
      // 后端按 strip + NFC 落盘，按真名选中新资产
      onCreated(normalizeAssetName(fields.name));
    },
    [projectName, assetType, onCreated, t],
  );
  const unit = useEditUnit<NewAssetFields>({ source: BLANK, save, leaveTitle: t(`assets:gallery_add.${assetType}`) });
  const { value, setValue } = unit;
  const submitting = unit.status === "saving";
  useEffect(() => {
    onSubmittingChange(submitting);
    // 创建成功时在请求结算前就切到详情，卸载时也要落下
    return () => onSubmittingChange(false);
  }, [submitting, onSubmittingChange]);
  const setField = <K extends keyof NewAssetFields>(key: K, next: NewAssetFields[K]) =>
    setValue((prev) => ({ ...prev, [key]: next }));
  const nameMissing = value.name.trim().length === 0;

  return (
    <form
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        if (!nameMissing) void unit.save();
      }}
    >
      <SheetHeader>
        <SheetTitle>{t(`assets:gallery_add.${assetType}`)}</SheetTitle>
      </SheetHeader>
      <SheetBody>
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor={nameId}>{t("assets:field.name")}</Label>
            <Input
              id={nameId}
              value={value.name}
              onChange={(e) => setField("name", e.target.value)}
              disabled={submitting}
              required
              autoComplete="off"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={descriptionId}>{t("assets:field.description")}</Label>
            <Textarea
              id={descriptionId}
              value={value.description}
              onChange={(e) => setField("description", e.target.value)}
              disabled={submitting}
              rows={4}
              placeholder={t(DESCRIPTION_PLACEHOLDER[assetType])}
            />
          </div>
          {assetType === "character" && (
            <div className="flex flex-col gap-2">
              <Label htmlFor={voiceStyleId}>{t("dashboard:voice_style")}</Label>
              <Input
                id={voiceStyleId}
                value={value.voiceStyle}
                onChange={(e) => setField("voiceStyle", e.target.value)}
                disabled={submitting}
                placeholder={t("dashboard:voice_style_example")}
              />
            </div>
          )}
          {assetType === "product" && (
            <>
              <div className="flex flex-col gap-2">
                <Label htmlFor={brandId}>{t("dashboard:product_brand_label")}</Label>
                <Input
                  id={brandId}
                  value={value.brand}
                  onChange={(e) => setField("brand", e.target.value)}
                  disabled={submitting}
                  placeholder={t("dashboard:product_brand_placeholder")}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={sellingPointsId}>{t("dashboard:product_selling_points_label")}</Label>
                <Textarea
                  id={sellingPointsId}
                  value={value.sellingPoints}
                  onChange={(e) => setField("sellingPoints", e.target.value)}
                  disabled={submitting}
                  rows={3}
                  placeholder={t("dashboard:product_selling_points_placeholder")}
                />
              </div>
            </>
          )}
          <p className="text-sm text-muted-foreground">{t(`assets:create_more_hint.${assetType}`)}</p>
        </div>
      </SheetBody>
      <SheetFooter className="flex-wrap">
        {unit.status === "error" ? (
          <p role="alert" className="mr-auto flex min-w-0 items-center gap-2 text-sm text-destructive">
            <AlertCircle aria-hidden className="size-4 shrink-0" />
            <span className="min-w-0 wrap-break-word">{t("assets:create_failed", { message: unit.error })}</span>
          </p>
        ) : null}
        <Button variant="outline" onClick={onCancel} disabled={submitting}>
          {t("common:cancel")}
        </Button>
        <Button type="submit" disabled={nameMissing || submitting}>
          {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
          {t("assets:create")}
        </Button>
      </SheetFooter>
    </form>
  );
}
