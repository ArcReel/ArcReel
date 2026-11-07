import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { Button } from "@/components/ui/button";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import type { AssetSheetType, WorkspaceFocusTarget } from "@/types";
import { errMsg } from "@/utils/async";
import { useAssetBusyNames } from "./assetBusyGuard";
import { AssetBrowseCard } from "./AssetBrowseCard";
import { AssetEditorSheet, type AssetEditorTarget } from "./AssetEditorSheet";
import { AssetImageViewer } from "./AssetImageViewer";
import { AssetSheetBatchControls } from "./AssetSheetBatchControls";
import { GalleryEmptyState } from "./GalleryEmptyState";
import { GalleryToolbar } from "./GalleryToolbar";
import {
  markerOf,
  matchesGalleryFilter,
  toGalleryAsset,
  type GalleryAssetSource,
  type GalleryFilter,
} from "./gallery-model";
import { useAssetSheetStatus, useSheetStatusByName } from "./useAssetSheetStatus";

export interface AssetGalleryProps<T extends GalleryAssetSource> {
  projectName: string;
  assetType: AssetSheetType;
  title: string;
  assets: Record<string, T>;
  /** Names of assets with running sheet tasks. */
  generatingNames?: Set<string>;
  /** Read-only onboarding demos omit creation/library insertion/generation/upload and other writes. */
  readOnly: boolean;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<unknown> | void;
  onReload?: () => Promise<unknown> | void;
  /**
   * Library-preview contents. Supplying this enables global-library interoperability: cards add to libraries/merge same-type assets,
   * toolbars select from libraries. Products do not enter libraries, so omit. Require stable references.
   */
  libraryPreview?: (asset: T) => LibraryImportPreview;
}

/**
 * Shared character/scene/prop/product gallery: toolbar, width-responsive browsing-card grid, detail Sheet, large-image viewer.
 * Gallery browses only: cards open details, secondary actions use more menus; adding opens blank detail forms.
 */
export function AssetGallery<T extends GalleryAssetSource>({
  projectName,
  assetType,
  title,
  assets,
  generatingNames,
  readOnly,
  onGenerate,
  onRestoreVersion,
  onReload,
  libraryPreview,
}: AssetGalleryProps<T>) {
  const { t } = useTranslation("assets");
  const occupiedNames = useAssetBusyNames(assetType, projectName);
  // Demo projects are absent from servers and have no sheet states to fetch.
  const demo = useDemoWorkbench();
  const rows = useAssetSheetStatus(projectName, !demo);
  const statusByName = useSheetStatusByName(rows, assetType);
  const [filter, setFilter] = useState<GalleryFilter>("all");
  const [editorTarget, setEditorTarget] = useState<AssetEditorTarget | null>(null);
  const [viewName, setViewName] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  // Card upload/version-restore/delete writes are mutually exclusive with viewer restoration.
  const [writingNames, setWritingNames] = useState<ReadonlySet<string>>(() => new Set());
  const handleWritingChange = useCallback((name: string, writing: boolean) => {
    setWritingNames((current) => {
      if (current.has(name) === writing) return current;
      const next = new Set(current);
      if (writing) next.add(name);
      else next.delete(name);
      return next;
    });
  }, []);

  const items = useMemo(
    () => Object.entries(assets).map(([name, source]) => ({ asset: toGalleryAsset(assetType, name, source), source })),
    [assets, assetType],
  );
  const previews = useMemo(
    () => (libraryPreview ? new Map(items.map(({ asset, source }) => [asset.name, libraryPreview(source)])) : null),
    [items, libraryPreview],
  );
  const shown = items.filter(({ asset }) => matchesGalleryFilter(asset, statusByName.get(asset.name), filter));
  const shownNames = shown.map(({ asset }) => asset.name);
  const filterCounts = {
    pending: items.filter(({ asset }) => matchesGalleryFilter(asset, statusByName.get(asset.name), "pending")).length,
    stale: items.filter(({ asset }) => matchesGalleryFilter(asset, statusByName.get(asset.name), "stale")).length,
  };

  // Agent asset changes locate its card; if filtered out, return to all first.
  const prepareTarget = useCallback(
    (target: WorkspaceFocusTarget) => {
      if (!(target.id in assets)) return false;
      setFilter("all");
      return true;
    },
    [assets],
  );
  useScrollTarget(assetType, { prepareTarget });

  const openAsset = useCallback((name: string) => setEditorTarget({ mode: "edit", name }), []);
  const onAdd = readOnly ? undefined : () => setEditorTarget({ mode: "create" });
  const viewAsset = useCallback((name: string) => setViewName(name), []);
  // Viewers switch only among assets with sheets, following filtered-grid order and card large-view eligibility.
  const viewable = shown
    .map(({ asset }) => asset)
    .filter((asset) => asset.sheetPath !== null && statusByName.get(asset.name)?.status !== "missing");
  // Close/end viewing when assets leave the viewable set; renewed eligibility must not reopen automatically.
  if (viewName !== null && !viewable.some((asset) => asset.name === viewName)) setViewName(null);
  const viewerBusy = new Set([...writingNames, ...occupiedNames]);
  generatingNames?.forEach((name) => viewerBusy.add(name));
  const editFromViewer = (name: string) => {
    setViewName(null);
    setEditorTarget({ mode: "edit", name });
  };

  const importable = libraryPreview !== undefined && assetType !== "product";
  const onPickFromLibrary = importable && !readOnly ? () => setPicking(true) : undefined;
  const handleImport = async (ids: string[]) => {
    try {
      await API.applyAssetsToProject({ asset_ids: ids, target_project: projectName, conflict_policy: "skip" });
      await onReload?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <section aria-label={title} className="relative flex min-h-0 flex-1 flex-col overflow-y-auto [scrollbar-gutter:stable]">
      <GalleryToolbar
        assetType={assetType}
        title={title}
        count={items.length}
        filter={filter}
        onFilterChange={setFilter}
        filterCounts={filterCounts}
        onAdd={onAdd}
        onPickFromLibrary={onPickFromLibrary}
      >
        {!readOnly && <AssetSheetBatchControls projectName={projectName} assetType={assetType} rows={rows} />}
      </GalleryToolbar>
      <div className="flex shrink-0 flex-col px-5 py-5" data-onboarding={ONBOARDING_ANCHORS.workbenchLorebook}>
        {items.length === 0 ? (
          <GalleryEmptyState
            assetType={assetType}
            onAdd={onAdd}
            onPickFromLibrary={onPickFromLibrary}
          />
        ) : shown.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
            <p>{t("gallery_no_match")}</p>
            <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
              {t("gallery_show_all")}
            </Button>
          </div>
        ) : (
          <ul aria-label={title} className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
            {shown.map(({ asset }) => {
              const row = statusByName.get(asset.name);
              const generating = generatingNames?.has(asset.name) ?? false;
              return (
                <li key={asset.name} className="flex min-w-0">
                  <AssetBrowseCard
                    projectName={projectName}
                    asset={asset}
                    sheetStatus={row}
                    marker={markerOf(asset, row, generating)}
                    generating={generating}
                    busy={occupiedNames.has(asset.name)}
                    readOnly={readOnly}
                    libraryPreview={previews?.get(asset.name)}
                    onOpen={openAsset}
                    onView={viewAsset}
                    onGenerate={onGenerate}
                    onRestoreVersion={onRestoreVersion}
                    onReload={onReload}
                    onWritingChange={handleWritingChange}
                    writingNames={writingNames}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <AssetEditorSheet
        projectName={projectName}
        assetType={assetType}
        assets={assets}
        target={editorTarget}
        onTargetChange={setEditorTarget}
        order={shownNames}
        statusByName={statusByName}
        generatingNames={generatingNames}
        writingNames={writingNames}
        readOnly={readOnly}
        onGenerate={onGenerate}
      />

      {picking && assetType !== "product" && (
        <AssetPickerModal
          type={assetType}
          existingNames={new Set(Object.keys(assets))}
          onClose={() => setPicking(false)}
          onImport={(ids) => void handleImport(ids)}
        />
      )}

      <AssetImageViewer
        projectName={projectName}
        assets={viewable}
        name={viewName}
        onNameChange={setViewName}
        onEdit={readOnly ? undefined : editFromViewer}
        busyNames={viewerBusy}
        readOnly={readOnly}
        onRestoreVersion={onRestoreVersion}
      />
    </section>
  );
}
