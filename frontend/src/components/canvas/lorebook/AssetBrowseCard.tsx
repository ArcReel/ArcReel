import { memo, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CloudDownload,
  CloudUpload,
  Combine,
  History,
  ImageUp,
  Images,
  Layers,
  Library,
  Maximize2,
  MoreHorizontal,
  PenLine,
  Sparkles,
  Tags,
  Trash2,
} from "lucide-react";
import { API } from "@/api";
import { AddToLibraryDialog, type LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { ProviderAssetBrowserDialog } from "@/components/assets/ProviderAssetBrowserDialog";
import { ProviderAssetUploadDialog } from "@/components/assets/ProviderAssetUploadDialog";
import { ASSET_SHEET_TYPE_ICON } from "@/components/assets/asset-type-icons";
import { CrossfadeImage } from "@/components/canvas/shared/CrossfadeImage";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { ImageEditDialog } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { isAssetBusy, rejectIfAssetBusy } from "./assetBusyGuard";
import { VERSION_RESOURCE, type GalleryAsset, type GalleryMarker } from "./gallery-model";
import { GalleryStatusMarker } from "./GalleryStatusMarker";
import { MergeAssetDialog } from "./MergeAssetDialog";
import { ProjectAssetDeleteDialog } from "./ProjectAssetDeleteDialog";
import { useStaleRegenerateConfirm } from "./useStaleRegenerateConfirm";
import type { AssetSheetStatusRow } from "@/types";

type DialogKey = "image-edit" | "versions" | "library" | "merge" | "delete" | "provider-assets" | "provider-upload";

export interface AssetBrowseCardProps {
  projectName: string;
  asset: GalleryAsset;
  /** Manifest decision for this sheet; until fetched infer from entry sheet-path registration. */
  sheetStatus: AssetSheetStatusRow | undefined;
  marker: GalleryMarker;
  /** Sheet tasks occupy this asset: generation or localized editing. */
  generating: boolean;
  /** Tasks occupy base/derivative files. */
  busy?: boolean;
  /** Read-only onboarding demos retain only large-image viewing, without deletion. */
  readOnly: boolean;
  /** Library-preview content, omitted for products excluded from libraries. */
  libraryPreview?: LibraryImportPreview;
  /** Card click opens asset details; require stable references. */
  onOpen: (name: string) => void;
  /** Large-image viewing; require stable references. */
  onView: (name: string) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<unknown> | void;
  onReload?: () => Promise<unknown> | void;
  /** Report card upload/version-restore/delete starts/finishes so gallery disables sibling asset writes; require stable references. */
  onWritingChange?: (name: string, writing: boolean) => void;
  /** All gallery cards' local writes, letting merge dialogs detect retained-asset occupancy. */
  writingNames?: ReadonlySet<string>;
}

/**
 * Shared browsing card for four asset types: viewing/selection only; editing occurs in details, secondary actions in more menus.
 */
export const AssetBrowseCard = memo(function AssetBrowseCard({
  projectName,
  asset,
  sheetStatus,
  marker,
  generating,
  busy: externalBusy = false,
  readOnly,
  libraryPreview,
  onOpen,
  onView,
  onGenerate,
  onRestoreVersion,
  onReload,
  onWritingChange,
  writingNames,
}: AssetBrowseCardProps) {
  const { t } = useTranslation(["assets", "dashboard"]);
  const titleId = useId();
  const moreRef = useRef<HTMLButtonElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const [dialog, setDialog] = useState<DialogKey | null>(null);
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const { type, name } = asset;
  const Icon = ASSET_SHEET_TYPE_ICON[type];

  const sheetFp = useProjectsStore((s) => (asset.sheetPath ? s.getAssetFingerprint(asset.sheetPath) : null));
  const sheetUrl =
    asset.sheetPath && sheetStatus?.status !== "missing"
      ? API.getFileUrl(projectName, asset.sheetPath, sheetFp, { width: 640 })
      : null;
  const imageUrl = sheetUrl && sheetUrl !== failedUrl ? sheetUrl : null;

  // Disable sibling actions together while generation/local editing/upload/version restore occupies sheets, or assets delete.
  const busy = externalBusy || generating || uploading || importing || restoring || deleting;
  const writing = uploading || importing || restoring || deleting;
  useEffect(() => {
    if (!writing || !onWritingChange) return;
    onWritingChange(name, true);
    return () => onWritingChange(name, false);
  }, [writing, name, onWritingChange]);
  // Merging is only for alias-capable library-interoperable types; products omit library previews.
  const mergeable = libraryPreview !== undefined && type !== "product";
  const describable = asset.description.trim().length > 0;
  const staleConfirm = useStaleRegenerateConfirm({
    projectName,
    assetType: type,
    name,
    status: sheetStatus,
    hasSheet: Boolean(asset.sheetPath),
    onGenerate: () => {
      if (!rejectIfAssetBusy(type, projectName, name, t, "assets:gallery_busy_hint")) onGenerate(name);
    },
  });

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || busy) return;
    if (rejectIfAssetBusy(type, projectName, name, t)) return;
    setUploading(true);
    try {
      await API.uploadFile(projectName, type, file, name);
      await onReload?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      // react-doctor-disable-next-line react-doctor/no-unowned-async-error-clear -- Busy disables/guards upload entry, so no newer same-card upload shares this flag; errors use notices, not local state
      setUploading(false);
    }
  };

  // Recheck before opening sheet-writing dialogs; Agents/other tabs may occupy resources after render snapshots.
  const openGuarded = (key: DialogKey) => {
    if (rejectIfAssetBusy(type, projectName, name, t, "assets:gallery_busy_hint")) return;
    setDialog(key);
  };
  const closeDialog = (open: boolean) => {
    if (!open) setDialog(null);
  };

  return (
    <article
      id={`${type}-${name}`}
      aria-labelledby={titleId}
      className="relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card transition-colors duration-fast hover:border-input"
    >
      <div className="relative flex aspect-video items-center justify-center overflow-hidden bg-muted text-muted-foreground">
        <CrossfadeImage
          src={imageUrl}
          alt=""
          className="object-contain"
          loading="lazy"
          onError={() => setFailedUrl(sheetUrl)}
          fallback={<Icon aria-hidden className="size-8" />}
        />
        <GalleryStatusMarker marker={marker} className="absolute top-2 left-2" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-3 pt-2.5">
        <div className="flex min-w-0 items-center gap-1">
          <h3 id={titleId} className="min-w-0 flex-1 text-sm font-medium">
            {/* The name button's pseudo-element covers the whole card to open details anywhere; More comes later in the DOM and uses its own positioning to sit above it. */}
            <button
              type="button"
              onClick={() => onOpen(name)}
              aria-label={name}
              className="block w-full text-left outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-3 focus-visible:after:ring-ring/50"
            >
              <TruncatedText text={name} focusable={false} />
            </button>
          </h3>
          <DropdownMenu>
            <DropdownMenuTrigger
              ref={moreRef}
              render={<Button variant="ghost" size="icon-sm" className="relative -mr-1.5 shrink-0" />}
              aria-label={t("assets:asset_menu_label", { name })}
            >
              <MoreHorizontal aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              <DropdownMenuGroup>
                <DropdownMenuItem disabled={!imageUrl} onClick={() => onView(name)}>
                  <Maximize2 aria-hidden />
                  {t("assets:gallery_menu_view")}
                </DropdownMenuItem>
                {!readOnly && (
                  <>
                    <DropdownMenuItem disabled={busy || !describable} onClick={staleConfirm.request}>
                      <Sparkles aria-hidden />
                      {asset.sheetPath ? t("dashboard:regenerate_design") : t("dashboard:generate_design")}
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={busy} onClick={() => uploadRef.current?.click()}>
                      <ImageUp aria-hidden />
                      {t("assets:upload_sheet")}
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={!asset.sheetPath} onClick={() => setDialog("provider-upload")}>
                      <CloudUpload aria-hidden />
                      {t("assets:provider_asset_upload")}
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={busy} onClick={() => openGuarded("provider-assets")}>
                      <CloudDownload aria-hidden />
                      {t("assets:provider_asset_select_from_library")}
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={busy || !imageUrl} onClick={() => openGuarded("image-edit")}>
                      <PenLine aria-hidden />
                      {t("dashboard:image_edit_action")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setDialog("versions")}>
                      <History aria-hidden />
                      {t("assets:gallery_menu_versions")}
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuGroup>
              {!readOnly && libraryPreview && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuItem disabled={busy} onClick={() => openGuarded("library")}>
                      <Library aria-hidden />
                      {t("assets:add_to_library")}
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={busy} onClick={() => openGuarded("merge")}>
                      <Combine aria-hidden />
                      {t("assets:merge_into")}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </>
              )}
              {!readOnly && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuItem variant="destructive" disabled={busy} onClick={() => openGuarded("delete")}>
                      <Trash2 aria-hidden />
                      {t("assets:delete")}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <p className="line-clamp-2 text-sm text-subtle-foreground">
          {asset.description.trim() || <span className="text-muted-foreground">{t("assets:no_description")}</span>}
        </p>
        <CardCounts asset={asset} />
      </div>

      {!readOnly && (
        <>
          <input
            ref={uploadRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:upload_sheet")}
            className="hidden"
            onChange={(event) => void handleUpload(event)}
          />
          {staleConfirm.dialog}
          <ProviderAssetBrowserDialog
            open={dialog === "provider-assets"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType={VERSION_RESOURCE[type]}
            resourceId={name}
            checkBusy={() => rejectIfAssetBusy(type, projectName, name, t, "assets:gallery_busy_hint") || busy}
            onImportingChange={setImporting}
            onImported={() => refreshAfterWrite(projectName, t)}
          />
          <ProviderAssetUploadDialog
            open={dialog === "provider-upload"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType={VERSION_RESOURCE[type]}
            resourceId={name}
          />
          <ImageEditDialog
            open={dialog === "image-edit"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType={type}
            resourceId={name}
            hasImage={Boolean(imageUrl)}
            busy={busy}
          />
          <VersionTimeMachine
            open={dialog === "versions"}
            onOpenChange={closeDialog}
            anchor={moreRef}
            projectName={projectName}
            resourceType={VERSION_RESOURCE[type]}
            resourceId={name}
            onRestore={onRestoreVersion}
            // Panels manage restoration in flight; disable for all other occupancy, including derivative tasks/gallery-aggregated external writes.
            busy={externalBusy || generating || uploading || deleting}
            onRestoringChange={setRestoring}
            checkBusy={() => isAssetBusy(type, projectName, name)}
          />
          <ProjectAssetDeleteDialog
            open={dialog === "delete"}
            onOpenChange={closeDialog}
            projectName={projectName}
            assetType={type}
            name={name}
            busy={externalBusy || generating || uploading || restoring}
            onDeletingChange={setDeleting}
            onMergeInstead={mergeable ? () => openGuarded("merge") : undefined}
          />
          {libraryPreview && type !== "product" && (
            <>
              <AddToLibraryDialog
                open={dialog === "library"}
                onOpenChange={closeDialog}
                resourceType={type}
                resourceId={name}
                projectName={projectName}
                preview={libraryPreview}
                busy={busy}
              />
              <MergeAssetDialog
                open={dialog === "merge"}
                onOpenChange={closeDialog}
                projectName={projectName}
                assetType={type}
                name={name}
                description={asset.description}
                busy={busy}
                writingNames={writingNames}
              />
            </>
          )}
        </>
      )}
    </article>
  );
});

/** Nonzero footer counts only: derivatives, aliases, and product originals. */
function CardCounts({ asset }: { asset: GalleryAsset }) {
  const { t } = useTranslation("assets");
  const items = [
    { key: "derivatives", count: asset.derivativeCount, Icon: Layers, label: t("derivatives_count", { count: asset.derivativeCount }) },
    { key: "aliases", count: asset.aliasCount, Icon: Tags, label: t("gallery_alias_count", { count: asset.aliasCount }) },
    { key: "references", count: asset.referenceCount, Icon: Images, label: t("gallery_reference_count", { count: asset.referenceCount }) },
  ].filter((item) => item.count > 0);
  if (items.length === 0) return null;
  return (
    <p className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 text-xs text-muted-foreground">
      {items.map(({ key, Icon, label }) => (
        <span key={key} className="inline-flex items-center gap-1">
          <Icon aria-hidden className="size-3.5" />
          {label}
        </span>
      ))}
    </p>
  );
}
