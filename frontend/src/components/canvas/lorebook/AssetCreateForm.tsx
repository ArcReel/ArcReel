import { useCallback, useId } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AlertCircle, Loader2 } from "lucide-react";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SheetBody, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import type { AssetSheetType } from "@/types";
import { errMsg } from "@/utils/async";
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

/** Restore same-name assets from project trash: newly created assets occupy originals, so restored names get suffixes and can merge later. */
async function restoreFromTrash(projectName: string, recordId: string, t: TFunction): Promise<void> {
  const { pushToast } = useAppStore.getState();
  try {
    const result = await API.restoreTrashRecord(projectName, recordId);
    if ((await refreshAfterWrite(projectName, t)) === "success") {
      pushToast(t("assets:create_trash_restored", { name: result.object.name }), "success");
    }
  } catch (error) {
    pushToast(t("assets:create_trash_restore_failed", { message: errMsg(error) }), "error");
  }
}

/**
 * Detail Sheet creation mode: blank form, creation saves. Callers switch to created asset details afterward,
 * where originals/audio/aliases are added. Closing filled forms invokes leave guards.
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
  /** Called after successful creation and project-data refresh. */
  onCreated: (name: string) => void;
  /** Creation in flight prevents Sheet closing. */
  onSubmittingChange: (submitting: boolean) => void;
}) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const nameId = useId();
  const descriptionId = useId();
  const voiceStyleId = useId();
  const brandId = useId();
  const sellingPointsId = useId();

  // Report occupancy where creation begins: success switches to details before settlement; form-unmounted finally still runs.
  const save = useCallback(
    async (fields: NewAssetFields) => {
      onSubmittingChange(true);
      try {
        const created = await createAsset(projectName, assetType, fields);
        // Refresh failures are reported; new assets remain absent from data, so close Sheet instead of leaving a repeat-submittable form.
        await refreshAfterWrite(projectName, t);
        // Backend persists strip + NFC; select new assets by their actual normalized names.
        const name = normalizeAssetName(fields.name);
        onCreated(name);
        const recordId = created.trash_record_id;
        if (recordId) {
          useAppStore.getState().pushToast(t("assets:create_trash_hint", { name }), "info", {
            action: {
              label: t("assets:create_trash_restore"),
              onClick: () => void restoreFromTrash(projectName, recordId, t),
            },
          });
        }
      } finally {
        onSubmittingChange(false);
      }
    },
    [projectName, assetType, onCreated, onSubmittingChange, t],
  );
  const unit = useEditUnit<NewAssetFields>({ source: BLANK, save, leaveTitle: t(`assets:gallery_add.${assetType}`) });
  const { value, setValue } = unit;
  const submitting = unit.status === "saving";
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
