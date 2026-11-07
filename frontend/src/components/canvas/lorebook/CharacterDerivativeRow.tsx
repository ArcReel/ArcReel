import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  CloudDownload,
  CloudUpload,
  History,
  ImageUp,
  Loader2,
  MoreHorizontal,
  PenLine,
  Pencil,
  Shirt,
  Trash2,
  X,
} from "lucide-react";
import { API, CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivativeResourceId } from "@/api";
import { enqueueCharacterDerivative } from "@/actions/generation";
import { ProviderAssetBrowserDialog } from "@/components/assets/ProviderAssetBrowserDialog";
import { ProviderAssetUploadDialog } from "@/components/assets/ProviderAssetUploadDialog";
import { ImageEditDialog } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { CrossfadeImage } from "@/components/canvas/shared/CrossfadeImage";
import { CopyButton } from "@/components/shared/CopyButton";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { TruncatedText } from "@/components/shared/TruncatedText";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { normalizeAssetName } from "@/utils/reference-mentions";
import type { AssetSheetStatusRow, CharacterDerivative, CharacterDerivativeStatus } from "@/types";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { isAssetBusy, rejectIfAssetBusy } from "./assetBusyGuard";
import { hasUsableDescription } from "./AssetSheetStatusBadge";
import type { GalleryMarker } from "./gallery-model";
import { GalleryStatusMarker } from "./GalleryStatusMarker";
import { GenerateButton } from "./GenerateButton";
import { useTrackWrite } from "./useAssetWrites";
import { useStaleRegenerateConfirm } from "./useStaleRegenerateConfirm";

/** Derivative reference marker used in script bodies. */
function derivativeToken(characterName: string, derivativeName: string): string {
  return `@[${characterName}/${derivativeName}]`;
}

type RowDialog = "image-edit" | "versions" | "delete" | "provider-assets" | "provider-upload";

export interface CharacterDerivativeRowProps {
  projectName: string;
  characterName: string;
  name: string;
  derivative: CharacterDerivative;
  /** Derivative image/outdated marker, undefined until fetched. */
  status: CharacterDerivativeStatus | undefined;
  /** Whether the base has a sheet; generated derivatives edit it and require it, while uploads do not. */
  ownerHasSheet: boolean;
  readOnly: boolean;
  /** Other detail writes/base generation occupy the asset; disable row writes together. */
  busy: boolean;
  onView: (image: { src: string; alt: string; title: string }) => void;
  /** Called after rename submission before project refresh, preserving this row's editing unit. */
  onRenamed: (from: string, to: string) => void;
  /** Reread derivative-image state after image/registration changes. */
  onSheetsChanged: () => void;
}

/**
 * Character-detail derivative row: thumbnail, name, copyable marker, script-reference state, appearance changes, generation entry.
 *
 * Appearance changes are this row's independent editing unit, saved inline alongside base editing; leave guards cover both.
 * Rename/delete/local editing/upload/provider-library selection/version restore execute immediately. Uploaded/selected images are finished media,
 * requiring no base sheet. Provider-library uploads/unlinking change association records only, not derivative images.
 */
export function CharacterDerivativeRow({
  projectName,
  characterName,
  name,
  derivative,
  status,
  ownerHasSheet,
  readOnly,
  busy,
  onView,
  onRenamed,
  onSheetsChanged,
}: CharacterDerivativeRowProps) {
  const { t } = useTranslation(["assets", "common", "dashboard"]);
  const descriptionId = useId();
  const moreRef = useRef<HTMLButtonElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const track = useTrackWrite();
  const [dialog, setDialog] = useState<RowDialog | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [importing, setImporting] = useState(false);
  const importSettled = useRef<(() => void) | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  const resourceId = derivativeResourceId(characterName, name);
  const generating = useActiveResourceIds("character_derivative", projectName).has(resourceId);
  const token = derivativeToken(characterName, name);
  const qualified = `${characterName}/${name}`;

  const save = useCallback(
    async (value: string) => {
      await track(
        API.updateCharacterDerivative(projectName, characterName, name, value).then(() =>
          refreshAfterWrite(projectName, t),
        ),
      );
      onSheetsChanged();
    },
    [track, projectName, characterName, name, onSheetsChanged, t],
  );
  const unit = useEditUnit<string>({
    source: derivative.description,
    save,
    leaveTitle: t("assets:edit_leave_title", { name: qualified }),
  });
  const saving = unit.status === "saving";
  // Disable siblings for row generation/version restore/import/rename/delete or writes elsewhere in details.
  const rowBusy = busy || generating || restoring || importing || pending;

  // Provider-library imports occur in dialogs; count their occupancy with detail uploads/deletes and other writes.
  const handleImportingChange = (value: boolean) => {
    setImporting(value);
    if (value) {
      void track(
        new Promise<void>((resolve) => {
          importSettled.current = resolve;
        }),
      );
    } else {
      importSettled.current?.();
      importSettled.current = null;
    }
  };
  useEffect(() => () => importSettled.current?.(), []);

  // Before state arrives, infer from registered images like gallery sheetStateOf, avoiding a pending-state flash.
  const artifactStatus = status
    ? (status.artifact_status ?? (status.stale ? "stale" : status.character_sheet ? "current" : "missing"))
    : derivative.character_sheet
      ? "current"
      : "missing";
  const sheetPath = artifactStatus === "missing" ? "" : (status?.character_sheet ?? derivative.character_sheet ?? "");
  const sheetFp = useProjectsStore((s) => (sheetPath ? s.getAssetFingerprint(sheetPath) : null));
  // Inline images use 640-width thumbnails; large viewers still use originals.
  const sheetUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp) : null;
  const thumbUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp, { width: 640 }) : null;
  const imageUrl = sheetUrl && thumbUrl !== failedUrl ? sheetUrl : null;
  const imageAlt = t("assets:library_derivative_thumb", { name: qualified });
  const describable = hasUsableDescription(unit.value);
  const marker: GalleryMarker = generating
    ? "generating"
    : !hasUsableDescription(derivative.description)
      ? "no-description"
      : artifactStatus === "stale"
        ? "stale"
        : sheetPath
          ? "current"
          : "missing";

  /** Submit-time recheck: queue occupancy for base/this derivative plus local in-flight detail writes. */
  const rejectIfBusy = () => {
    if (rowBusy) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return true;
    }
    return (
      rejectIfAssetBusy("character", projectName, characterName, t, "assets:derivative_busy_hint") ||
      rejectIfAssetBusy("character_derivative", projectName, resourceId, t, "assets:derivative_busy_hint")
    );
  };

  const handleGenerate = async () => {
    if (
      isAssetBusy("character", projectName, characterName) ||
      isResourceBusy("character_derivative", projectName, resourceId)
    ) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return;
    }
    try {
      await enqueueCharacterDerivative(projectName, characterName, name);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  };

  const sheetStatus: AssetSheetStatusRow = {
    unit_id: `character/${resourceId}`,
    asset_type: "character",
    name: characterName,
    derivative: name,
    status: artifactStatus,
    description_missing: !hasUsableDescription(derivative.description),
    image_to_image: true,
  };
  const staleConfirm = useStaleRegenerateConfirm({
    projectName,
    assetType: "character",
    name: characterName,
    derivativeName: name,
    status: sheetStatus,
    hasSheet: Boolean(sheetPath),
    onGenerate: () => void handleGenerate(),
  });

  const generate = () => {
    // Recheck before submission; bases/derivatives may become occupied after rendering and before clicking.
    if (rejectIfBusy()) return;
    // Derivatives depend on appearance changes; edits outdate images after saving, before server decisions can observe them.
    const willBeStale = unit.value !== unit.savedValue;
    void unit.saveAndGenerate(handleGenerate, {
      confirm: async () => {
        if (!(await staleConfirm.confirm({ willBeStale }))) return false;
        return !rejectIfBusy();
      },
    });
  };

  /** Run an immediate write; show failure reasons and return false. */
  const run = async (work: () => Promise<unknown>): Promise<boolean> => {
    if (rejectIfBusy()) return false;
    setPending(true);
    try {
      await track(work());
      onSheetsChanged();
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      return false;
    } finally {
      setPending(false);
    }
  };

  const startRename = () => {
    if (rejectIfBusy()) return;
    setRenameDraft(name);
    setRenaming(true);
    // Menus restore focus to more buttons on close; wait before moving focus to inputs.
    requestAnimationFrame(() => renameInputRef.current?.select());
  };

  const submitRename = async () => {
    // Match backend strip + NFC; row mappings register actual persisted names.
    const next = normalizeAssetName(renameDraft);
    if (!next || next === name) {
      setRenaming(false);
      return;
    }
    const ok = await run(() =>
      API.renameCharacterDerivative(projectName, characterName, name, next).then(() => {
        onRenamed(name, next);
        return refreshAfterWrite(projectName, t);
      }),
    );
    if (ok) setRenaming(false);
  };

  const handleDelete = async () => {
    const ok = await run(() =>
      API.deleteCharacterDerivative(projectName, characterName, name).then(() => {
        // Deletion confirmation includes appearance changes; discard row drafts instead of retaining them as external deletion.
        unit.discard();
        return refreshAfterWrite(projectName, t);
      }),
    );
    if (ok) setDialog(null);
  };

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await run(() =>
      API.uploadCharacterDerivativeSheet(projectName, characterName, name, file).then((result) => {
        useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
        return refreshAfterWrite(projectName, t);
      }),
    );
  };

  // Recheck occupancy before opening derivative-writing dialogs.
  const openGuarded = (key: RowDialog) => {
    if (!rejectIfBusy()) setDialog(key);
  };
  const closeDialog = (open: boolean) => {
    if (!open) setDialog(null);
  };

  const thumbnail = (
    <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-md border border-border bg-muted/40">
      {imageUrl ? (
        <button
          type="button"
          onClick={() => onView({ src: imageUrl, alt: imageAlt, title: qualified })}
          aria-label={t("assets:view_image", { name: imageAlt })}
          className="focus-ring block size-full"
        >
          <CrossfadeImage
            src={thumbUrl}
            alt={imageAlt}
            className="object-contain"
            loading="lazy"
            onError={() => setFailedUrl(thumbUrl)}
            fallback={null}
          />
        </button>
      ) : (
        <div className="flex size-full items-center justify-center text-muted-foreground">
          <Shirt aria-hidden className="size-5" />
        </div>
      )}
      <GalleryStatusMarker marker={marker} className="absolute top-1 left-1" />
    </div>
  );

  return (
    <li className="flex flex-col gap-2 p-3">
      <div className="flex min-w-0 gap-3">
        {thumbnail}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {renaming ? (
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                void submitRename();
              }}
            >
              <Input
                ref={renameInputRef}
                aria-label={t("assets:derivative_rename_label", { name })}
                value={renameDraft}
                disabled={pending}
                onChange={(e) => setRenameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    // Esc exits rename only, without closing the outer Sheet.
                    e.preventDefault();
                    e.stopPropagation();
                    if (!pending) setRenaming(false);
                  }
                }}
                className="h-7 min-w-0 flex-1"
              />
              <Button type="submit" variant="ghost" size="icon-sm" disabled={pending} aria-label={t("assets:derivative_rename_confirm")}>
                {pending ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={pending}
                onClick={() => setRenaming(false)}
                aria-label={t("common:cancel")}
              >
                <X aria-hidden />
              </Button>
            </form>
          ) : (
            <div className="flex min-h-7 min-w-0 items-center gap-1">
              <h4 className="min-w-0 flex-1 text-sm font-medium">
                <TruncatedText text={name} />
              </h4>
              {readOnly ? null : (
                <DropdownMenu>
                  <DropdownMenuTrigger
                    ref={moreRef}
                    render={<Button variant="ghost" size="icon-sm" className="shrink-0" />}
                    aria-label={t("assets:derivative_menu_label", { name })}
                  >
                    <MoreHorizontal aria-hidden />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="min-w-40">
                    <DropdownMenuGroup>
                      <DropdownMenuItem disabled={rowBusy} onClick={startRename}>
                        <Pencil aria-hidden />
                        {t("assets:derivative_rename")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={rowBusy}
                        onClick={() => {
                          // Recheck before file selection; run rechecks again after files are chosen on submission.
                          if (!rejectIfBusy()) uploadRef.current?.click();
                        }}
                      >
                        <ImageUp aria-hidden />
                        {t("assets:derivative_upload")}
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={!sheetPath} onClick={() => setDialog("provider-upload")}>
                        <CloudUpload aria-hidden />
                        {t("assets:provider_asset_upload")}
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={rowBusy} onClick={() => openGuarded("provider-assets")}>
                        <CloudDownload aria-hidden />
                        {t("assets:provider_asset_select_from_library")}
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={rowBusy || !imageUrl} onClick={() => openGuarded("image-edit")}>
                        <PenLine aria-hidden />
                        {t("dashboard:image_edit_action")}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setDialog("versions")}>
                        <History aria-hidden />
                        {t("assets:gallery_menu_versions")}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuItem variant="destructive" disabled={rowBusy} onClick={() => openGuarded("delete")}>
                        <Trash2 aria-hidden />
                        {t("assets:delete")}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          )}
          <div className="flex min-w-0 items-center gap-1">
            <code className="min-w-0 truncate rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs text-subtle-foreground">
              {token}
            </code>
            <CopyButton text={token} label={t("assets:derivative_copy_token", { token })} />
          </div>
          {derivative.referenced === undefined ? null : (
            <p className="text-xs text-muted-foreground">
              {t(derivative.referenced ? "assets:derivative_referenced" : "assets:derivative_unreferenced")}
            </p>
          )}
        </div>
      </div>

      <Textarea
        id={descriptionId}
        aria-label={t("assets:derivative_description_of", { name })}
        placeholder={t("assets:derivative_description_placeholder")}
        value={unit.value}
        readOnly={readOnly}
        onChange={(e) => unit.setValue(e.target.value)}
        rows={2}
      />

      {readOnly ? null : (
        <>
          <UnsavedChangesBar unit={unit} />
          <div className="flex flex-wrap items-center gap-1.5">
            <GenerateButton
              onClick={generate}
              loading={generating}
              disabled={rowBusy || saving || !ownerHasSheet || !describable}
              label={
                unit.dirty
                  ? t("common:save_and_generate")
                  : sheetPath
                    ? t("assets:derivative_regenerate")
                    : t("assets:derivative_generate")
              }
              size="sm"
              variant="outline"
            />
            <PromptPreviewButton
              title={t("assets:prompt_preview_title", { name: qualified })}
              beforeOpen={unit.save}
              saveFirst={unit.dirty}
              // Do not start concurrent second saves while saving.
              disabled={saving}
              load={(signal) =>
                API.previewAssetPrompt(projectName, "character", characterName, unit.value, {
                  signal,
                  derivativeName: name,
                })
              }
            />
          </div>
          {ownerHasSheet ? (
            describable ? null : (
              <p className="text-xs text-muted-foreground">{t("assets:sheet_description_required")}</p>
            )
          ) : (
            <p className="text-xs text-muted-foreground">{t("assets:derivative_owner_sheet_required")}</p>
          )}

          <input
            ref={uploadRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:derivative_upload_of", { name: qualified })}
            className="hidden"
            onChange={(event) => void handleUpload(event)}
          />
          {staleConfirm.dialog}
          <ProviderAssetBrowserDialog
            open={dialog === "provider-assets"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType={CHARACTER_DERIVATIVE_RESOURCE_TYPE}
            resourceId={resourceId}
            checkBusy={rejectIfBusy}
            onImportingChange={handleImportingChange}
            onImported={async () => {
              const result = await refreshAfterWrite(projectName, t);
              onSheetsChanged();
              return result;
            }}
          />
          <ProviderAssetUploadDialog
            open={dialog === "provider-upload"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType={CHARACTER_DERIVATIVE_RESOURCE_TYPE}
            resourceId={resourceId}
          />
          <ImageEditDialog
            open={dialog === "image-edit"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType="character_derivative"
            resourceId={resourceId}
            hasImage={Boolean(imageUrl)}
            busy={rowBusy}
          />
          <VersionTimeMachine
            open={dialog === "versions"}
            onOpenChange={closeDialog}
            anchor={moreRef}
            projectName={projectName}
            resourceType={CHARACTER_DERIVATIVE_RESOURCE_TYPE}
            resourceId={resourceId}
            onRestore={async () => {
              await refreshAfterWrite(projectName, t);
              onSheetsChanged();
            }}
            busy={busy || generating || importing || pending}
            onRestoringChange={setRestoring}
            checkBusy={() => isAssetBusy("character_derivative", projectName, resourceId)}
          />
          <AlertDialog
            open={dialog === "delete"}
            onOpenChange={(open) => {
              // Ignore closing during deletion.
              if (!open && !pending) setDialog(null);
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("assets:derivative_delete_title", { name })}</AlertDialogTitle>
              </AlertDialogHeader>
              <AlertDialogBody tabIndex={0} role="region" aria-label={t("assets:derivative_delete_title", { name })}>
                <AlertDialogDescription>
                  {t(
                    derivative.referenced
                      ? "assets:derivative_delete_description_referenced"
                      : "assets:derivative_delete_description",
                    { token },
                  )}
                </AlertDialogDescription>
              </AlertDialogBody>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={pending}>{t("common:cancel")}</AlertDialogCancel>
                <AlertDialogAction variant="destructive" disabled={rowBusy} onClick={() => void handleDelete()}>
                  {pending ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                  {t("assets:delete")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </li>
  );
}
