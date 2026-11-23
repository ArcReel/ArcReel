import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isAssetBusy } from "@/components/canvas/lorebook/assetBusyGuard";
import { errMsg } from "@/utils/async";
import type { AssetIdentity, AssetType } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";

export interface LibraryImportPreview {
  description: string;
  voiceStyle?: string;
  hasReferenceAudio?: boolean;
  sheetPath?: string | null;
  derivativeCount?: number;
}

interface Props {
  resourceType: AssetType;
  resourceId: string;
  projectName: string;
  preview: LibraryImportPreview;
  busy?: boolean;
}

interface AssetLookup {
  name: string;
  asset: AssetIdentity | null;
}

function matchesConflictTarget(value: unknown, type: AssetType, name: string): value is AssetIdentity {
  if (!value || typeof value !== "object") return false;
  const asset = value as Partial<AssetIdentity>;
  return typeof asset.id === "string" && !!asset.id && asset.type === type && asset.name === name.normalize("NFC");
}

function getConflictAsset(
  name: string,
  lookup: AssetLookup | null,
  submittedConflict: AssetLookup | null,
): AssetIdentity | null {
  if (submittedConflict?.name === name) return submittedConflict.asset;
  return lookup?.name === name ? lookup.asset : null;
}

export function AddToLibraryDialog({
  open,
  onOpenChange,
  busy = false,
  ...props
}: Props & { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [submitting, setSubmitting] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && submitting) return;
        onOpenChange(next);
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <AddToLibraryForm
          key={`${props.resourceType}:${props.resourceId}`}
          {...props}
          busy={busy}
          submitting={submitting}
          setSubmitting={setSubmitting}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function AddToLibraryForm({
  resourceType,
  resourceId,
  projectName,
  preview,
  busy,
  submitting,
  setSubmitting,
  onDone,
}: Props & {
  busy: boolean;
  submitting: boolean;
  setSubmitting: (submitting: boolean) => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("assets");
  const nameId = useId();
  // react-doctor-disable-next-line react-doctor/no-derived-useState -- 资源名只作可编辑名称的初值，之后由用户修改；资源变化时调用处按 key 重建表单
  const [name, setName] = useState(resourceId);
  const [lookup, setLookup] = useState<AssetLookup | null>(null);
  const [submittedConflict, setSubmittedConflict] = useState<AssetLookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const trimmedName = name.trim();
  const checkedName = useDebouncedValue(trimmedName, 250);
  const sheetFp = useProjectsStore((s) => (preview.sheetPath ? s.getAssetFingerprint(preview.sheetPath) : null));
  const imageUrl = preview.sheetPath ? API.getFileUrl(projectName, preview.sheetPath, sheetFp) : null;
  const Icon = ASSET_TYPE_ICON[resourceType];
  const typeLabel = t(`type.${resourceType}`);
  const isCharacter = resourceType === "character";

  useEffect(() => {
    if (!checkedName) return;
    const controller = new AbortController();
    API.findAssetByName(resourceType, checkedName, controller.signal).then(
      ({ asset }) => {
        if (controller.signal.aborted) return;
        setLookup({ name: checkedName, asset });
      },
      () => {},
    );
    return () => controller.abort();
  }, [checkedName, resourceType]);

  const conflictAsset = getConflictAsset(trimmedName, lookup, submittedConflict);

  const submit = async (target?: AssetIdentity) => {
    if (!trimmedName || submitting) return;
    if (busy || isAssetBusy(resourceType, projectName, resourceId)) {
      setError(t("add_to_library_busy_hint"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await API.addAssetFromProject({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        override_name: trimmedName !== resourceId ? trimmedName : undefined,
        overwrite: target !== undefined,
        overwrite_asset_id: target?.id,
      });
      useAppStore.getState().pushToast(t("add_to_library_success", { name: trimmedName }), "success");
      onDone();
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 409 && err.detail
        && typeof err.detail === "object" && "existing" in err.detail) {
        const { existing } = err.detail;
        if (matchesConflictTarget(existing, resourceType, trimmedName)) {
          setSubmittedConflict({ name: trimmedName, asset: existing });
          if (!target) return;
        } else if (existing === null) {
          setSubmittedConflict({ name: trimmedName, asset: null });
        }
      }
      setError(t("add_to_library_failed", { message: errMsg(err) }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("import_title", { name: resourceId })}</DialogTitle>
        <DialogDescription>{t("import_description")}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <div className="flex flex-col gap-5">
          <AssetThumb
            imageUrl={imageUrl}
            alt={t("asset_image_alt", { name: resourceId })}
            fallback={<span className="flex flex-col items-center gap-2 text-sm"><Icon aria-hidden className="size-8" />{t("no_sheet")}</span>}
            className="rounded-lg border border-border"
          />
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>{t("field.name")}</Label>
            <Input
              id={nameId}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setSubmittedConflict(null);
                setError(null);
              }}
              autoComplete="off"
              disabled={submitting}
            />
            {conflictAsset && (
              <p className="flex items-start gap-1.5 text-sm text-subtle-foreground">
                <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                {t("conflict_warning", { type: typeLabel, name: conflictAsset.name })}
              </p>
            )}
          </div>
          <dl className="flex flex-col gap-4 text-sm">
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted-foreground">{t("field.description")}</dt>
              <dd className="whitespace-pre-wrap break-words">
                {preview.description || <span className="text-muted-foreground">{t("no_description")}</span>}
              </dd>
            </div>
            {isCharacter && (
              <>
                <div className="flex flex-col gap-1">
                  <dt className="text-xs font-medium text-muted-foreground">{t("voice")}</dt>
                  <dd className="break-words">
                    {preview.voiceStyle || <span className="text-muted-foreground">{t("voice_style_none")}</span>}
                  </dd>
                  <dd className="text-muted-foreground">
                    {t(preview.hasReferenceAudio ? "reference_audio_included" : "reference_audio_none")}
                  </dd>
                </div>
                <div className="flex flex-col gap-1">
                  <dt className="text-xs font-medium text-muted-foreground">{t("derivatives")}</dt>
                  <dd>{t("derivatives_count", { count: preview.derivativeCount ?? 0 })}</dd>
                </div>
              </>
            )}
          </dl>
          <p className="text-xs text-muted-foreground">{t("import_readonly_hint")}</p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={submitting} />}>{t("cancel")}</DialogClose>
        {conflictAsset && (
          <Button variant="destructive" disabled={busy || submitting} onClick={() => void submit(conflictAsset)}>
            {t("overwrite_existing")}
          </Button>
        )}
        <Button disabled={!trimmedName || submitting || conflictAsset !== null} onClick={() => void submit()}>
          {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {t("confirm_import")}
        </Button>
      </DialogFooter>
    </>
  );
}
