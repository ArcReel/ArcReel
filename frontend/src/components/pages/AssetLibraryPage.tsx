import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Search } from "lucide-react";
import { ApplyToProjectDialog } from "@/components/assets/ApplyToProjectDialog";
import { AssetCard, type AssetCardAction } from "@/components/assets/AssetCard";
import { AssetCreateDialog } from "@/components/assets/AssetCreateDialog";
import { AssetDetailSheet, type AssetSheetMode } from "@/components/assets/AssetDetailSheet";
import { ASSET_TYPE_ICON, ASSET_TYPES } from "@/components/assets/asset-type-icons";
import { DeleteAssetDialog } from "@/components/assets/DeleteAssetDialog";
import { LoadMoreSentinel } from "@/components/assets/LoadMoreSentinel";
import { useAssetPages, type AssetPages } from "@/components/assets/useAssetPages";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { PageShell } from "@/components/shared/page-shell/PageShell";
import { useReturnTo } from "@/components/shared/page-shell/return-to";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { Asset, AssetType } from "@/types/asset";

function isAssetType(value: string | null): value is AssetType {
  return value === "character" || value === "scene" || value === "prop";
}

/**
 * 全局资产库：铺满档的页面外壳，类型标签显示当前搜索下各类型的匹配数，网格滚动到底时自动加载下一页。
 * 当前类型与搜索词记在地址的 `tab`、`q` 参数里。点卡片打开详情 Sheet，在详情里点「编辑」才进入表单。
 */
export function AssetLibraryPage() {
  const { t } = useTranslation(["assets", "common"]);
  const [pathname, navigate] = useLocation();
  const search = useSearch();
  const goBack = useReturnTo();

  const params = useMemo(() => new URLSearchParams(search), [search]);
  const tabParam = params.get("tab");
  const activeTab: AssetType = isAssetType(tabParam) ? tabParam : "character";
  const urlQ = params.get("q") ?? "";

  const writeQuery = useCallback(
    (patch: { tab?: AssetType; q?: string }) => {
      const next = new URLSearchParams(search);
      if (patch.tab !== undefined) {
        if (patch.tab === "character") next.delete("tab");
        else next.set("tab", patch.tab);
      }
      if (patch.q !== undefined) {
        if (patch.q) next.set("q", patch.q);
        else next.delete("q");
      }
      const qs = next.toString();
      navigate(qs ? `${pathname}?${qs}` : pathname, { replace: true });
    },
    [navigate, pathname, search],
  );

  const [q, setQ] = useState(urlQ);
  const debouncedQ = useDebouncedValue(q, 250);

  // 浏览器前进/后退或外部地址栏变化导致 urlQ 改变时，把 URL 当前值同步到本地 q。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部 URL 变化驱动本地 q 同步，functional setState 已做幂等保护
    setQ((prev) => (prev === urlQ ? prev : urlQ));
  }, [urlQ]);

  // debouncedQ 与 URL 不一致时回写，使用 urlQ（已订阅 search）做对比，避免覆盖外部变更。
  useEffect(() => {
    if (urlQ === debouncedQ) return;
    writeQuery({ q: debouncedQ });
  }, [debouncedQ, urlQ, writeQuery]);

  const searchTerm = urlQ.trim();
  const pages = useAssetPages({ type: activeTab, q: searchTerm });
  const { add, update, remove } = pages;

  const [detail, setDetail] = useState<{ id: string; mode: AssetSheetMode } | null>(null);
  const [creatingType, setCreatingType] = useState<AssetType | null>(null);
  const [applyTarget, setApplyTarget] = useState<Asset | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);
  const detailAsset = detail ? (pages.items.find((asset) => asset.id === detail.id) ?? null) : null;

  const handleCardAction = useCallback((action: AssetCardAction, asset: Asset) => {
    if (action === "open") setDetail({ id: asset.id, mode: "view" });
    else if (action === "edit") setDetail({ id: asset.id, mode: "edit" });
    else if (action === "apply") setApplyTarget(asset);
    else setDeleteTarget(asset);
  }, []);

  const createButton = (
    <Button onClick={() => setCreatingType(activeTab)}>
      <Plus aria-hidden data-icon="inline-start" />
      {t("add_asset")}
    </Button>
  );

  return (
    <PageShell
      tier="full"
      header={
        <PageHeader
          back={{ label: t("common:back"), onClick: goBack }}
          title={t("library_title")}
          subtitle={t("library_subtitle")}
          actions={
            <>
              <InputGroup className="w-56">
                <InputGroupAddon>
                  <Search aria-hidden />
                </InputGroupAddon>
                <InputGroupInput
                  type="search"
                  aria-label={t("search_label")}
                  placeholder={t("search_placeholder")}
                  value={q}
                  onChange={(event) => setQ(event.target.value)}
                />
              </InputGroup>
              {createButton}
            </>
          }
        />
      }
    >
      <Tabs value={activeTab} onValueChange={(next: AssetType) => writeQuery({ tab: next })}>
        <div className="border-b border-border">
          <TabsList variant="line" aria-label={t("library_tabs_label")} activateOnFocus>
            {ASSET_TYPES.map((type) => {
              const Icon = ASSET_TYPE_ICON[type];
              const count = pages.counts?.[type];
              return (
                <TabsTrigger key={type} value={type}>
                  <Icon aria-hidden data-icon="inline-start" />
                  {t(`type.${type}`)}
                  {count !== undefined && <span className="text-muted-foreground tabular-nums">{count}</span>}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </div>
        {ASSET_TYPES.map((type) => (
          <TabsContent key={type} value={type} className="mt-4">
            {type === activeTab && (
              <LibraryPanel
                type={type}
                pages={pages}
                searchTerm={searchTerm}
                createButton={createButton}
                onCardAction={handleCardAction}
              />
            )}
          </TabsContent>
        ))}
      </Tabs>

      <AssetDetailSheet
        asset={detailAsset}
        mode={detail?.mode ?? "view"}
        onModeChange={(mode) => setDetail((prev) => (prev ? { ...prev, mode } : prev))}
        onClose={() => setDetail(null)}
        onApply={setApplyTarget}
        onDelete={setDeleteTarget}
        onChanged={update}
      />
      <AssetCreateDialog type={creatingType} onClose={() => setCreatingType(null)} onCreated={add} />
      <ApplyToProjectDialog asset={applyTarget} onClose={() => setApplyTarget(null)} />
      <DeleteAssetDialog asset={deleteTarget} onClose={() => setDeleteTarget(null)} onDeleted={remove} />
    </PageShell>
  );
}

function LibraryPanel({
  type,
  pages,
  searchTerm,
  createButton,
  onCardAction,
}: {
  type: AssetType;
  pages: AssetPages;
  searchTerm: string;
  createButton: ReactNode;
  onCardAction: (action: AssetCardAction, asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const Icon = ASSET_TYPE_ICON[type];

  if (pages.loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {t("loading")}
      </div>
    );
  }
  if (pages.error) {
    return (
      <div className="flex flex-col items-center gap-3 py-24 text-center">
        <p role="alert" className="text-sm text-destructive">
          {t("library_load_failed", { message: pages.error })}
        </p>
        <Button variant="outline" onClick={pages.retry}>
          {t("retry")}
        </Button>
      </div>
    );
  }
  if (pages.items.length === 0) {
    return searchTerm ? (
      <p className="py-24 text-center text-sm text-muted-foreground">
        {t("library_no_match", { type: t(`type.${type}`), query: searchTerm })}
      </p>
    ) : (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border py-20 text-center">
        <span className="flex size-10 items-center justify-center rounded-full bg-primary/15 text-primary">
          <Icon aria-hidden className="size-5" />
        </span>
        <p className="text-base font-medium">{t(`library_empty_${type}`)}</p>
        <p className="max-w-sm text-sm text-muted-foreground">{t("library_empty_hint")}</p>
        {createButton}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {pages.items.map((asset) => (
          <li key={asset.id} className="flex min-w-0">
            <AssetCard asset={asset} onAction={onCardAction} />
          </li>
        ))}
      </ul>
      <LoadMoreSentinel
        hasMore={pages.hasMore}
        loading={pages.loadingMore}
        error={pages.moreError}
        itemCount={pages.items.length}
        onReach={pages.loadMore}
        onRetry={pages.retry}
      />
    </div>
  );
}
