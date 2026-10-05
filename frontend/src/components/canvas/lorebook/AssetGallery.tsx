import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { Button } from "@/components/ui/button";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
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
  /** 有资产图任务在跑的资产名。 */
  generatingNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增、入库、生成、上传等改写入口。 */
  readOnly: boolean;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<unknown> | void;
  onReload?: () => Promise<unknown> | void;
  /**
   * 入库预览的内容。传入即表示这类资产与全局资产库互通：卡片可加入资产库、并入同类资产，
   * 工具栏可从资产库选择。商品不入资产库，不传。需传稳定引用。
   */
  libraryPreview?: (asset: T) => LibraryImportPreview;
}

/**
 * 角色、场景、道具、商品共用的画廊：工具栏、按画布宽度加列的浏览卡网格、详情 Sheet 与大图查看。
 * 画廊只负责浏览，点卡片打开详情，次要操作在卡片的「更多」里；「添加」在详情 Sheet 里打开空白表单。
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
  const rows = useAssetSheetStatus(projectName);
  const statusByName = useSheetStatusByName(rows, assetType);
  const [filter, setFilter] = useState<GalleryFilter>("all");
  const [editorTarget, setEditorTarget] = useState<AssetEditorTarget | null>(null);
  const [viewName, setViewName] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  // 卡片上在途的上传、版本恢复与删除：查看器里的还原与它们互斥
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

  // Agent 改动某个资产时定位到它的卡片；被筛选藏起来时先回到「全部」。
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
  // 查看器只在有资产图的资产之间切换，顺序与筛选后的网格一致；判据与卡片的「查看大图」相同
  const viewable = shown
    .map(({ asset }) => asset)
    .filter((asset) => asset.sheetPath !== null && statusByName.get(asset.name)?.status !== "missing");
  // 查看中的资产离开可查看列表时查看器随之关闭，同时结束查看：之后它重新可查看也不再自行弹出
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
