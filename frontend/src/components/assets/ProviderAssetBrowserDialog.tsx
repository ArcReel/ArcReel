import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { providerAssetStatusLabelKey, siteLabelKey } from "@/components/pages/settings/asset-library/asset-library-labels";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAppStore } from "@/stores/app-store";
import type { RefreshProjectResult } from "@/stores/projects-store";
import type { AssetLibraryAccount, ProviderAsset, ProviderAssetImportResult, ProviderAssetPage } from "@/types";
import { cn } from "cn";

const PAGE_SIZE = 20;

const TYPE_LABEL_KEYS: Record<string, string> = {
  Image: "provider_asset_type_image",
  Video: "provider_asset_type_video",
  Audio: "provider_asset_type_audio",
};

const STATUS_CLS: Record<ProviderAsset["status"], string> = {
  active: "text-good",
  processing: "text-muted-foreground",
  failed: "text-destructive",
  unknown: "text-muted-foreground",
};

type AccountsState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; accounts: AssetLibraryAccount[] };

type PageState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; page: ProviderAssetPage };

export interface ProviderAssetBrowserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  /** 版本历史里的资源类型，如 `characters`。 */
  resourceType: string;
  resourceId: string;
  /** 导入成功后调用，用于刷新资产图；对话框随后关闭。 */
  onImported: (result: ProviderAssetImportResult) => Promise<RefreshProjectResult>;
  checkBusy: () => boolean;
  onImportingChange: (importing: boolean) => void;
}

type ImportTarget = Omit<ProviderAssetBrowserDialogProps, "open" | "onOpenChange" | "onImportingChange">;

/**
 * 从供应商素材库选择：按所选素材库账号的站点、区域与项目范围分页列出素材，选择一张可用的图片后，
 * ArcReel 取回原图保存为这张资产图的新版本，并为该版本建立关联。素材库账号在全局设置「素材库与存储」中配置。
 * 导入进行中不能关闭对话框，避免误以为已取消。
 */
export function ProviderAssetBrowserDialog({
  open,
  onOpenChange,
  onImportingChange,
  ...target
}: ProviderAssetBrowserDialogProps) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const [importing, setImporting] = useState(false);
  const handleOpenChange = (next: boolean) => {
    if (!next && importing) return;
    onOpenChange(next);
  };
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("provider_asset_dialog_title")}</DialogTitle>
          <DialogDescription>{t("provider_asset_dialog_desc")}</DialogDescription>
        </DialogHeader>
        {open && (
          <BrowserBody
            target={target}
            onImportingChange={(value) => {
              setImporting(value);
              onImportingChange(value);
            }}
            onDone={() => onOpenChange(false)}
          />
        )}
        <DialogFooter>
          <Button variant="outline" disabled={importing} onClick={() => onOpenChange(false)}>
            {t("common:close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function selectable(asset: ProviderAsset): boolean {
  return asset.asset_type === "Image" && asset.status === "active";
}

function BrowserBody({
  target,
  onImportingChange,
  onDone,
}: {
  target: ImportTarget;
  onImportingChange: (importing: boolean) => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const accountLabelId = useId();
  const [accounts, setAccounts] = useState<AccountsState>({ status: "loading" });
  const [accountId, setAccountId] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [page, setPage] = useState<PageState>({ status: "idle" });
  const [importingId, setImportingId] = useState<string | null>(null);
  const [importError, setImportError] = useState<unknown>(null);

  const accountsController = useRef<AbortController | null>(null);
  const fetchAccounts = useCallback(async () => {
    accountsController.current?.abort();
    const controller = new AbortController();
    accountsController.current = controller;
    setAccounts({ status: "loading" });
    try {
      const data = await API.listAssetLibraryAccounts({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setAccounts({ status: "ready", accounts: data.accounts });
      setAccountId((current) => current ?? data.accounts[0]?.id ?? null);
    } catch (error) {
      if (controller.signal.aborted) return;
      setAccounts({ status: "error", error });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 打开对话框时拉取素材库账号后回写，属于受控的初始化加载
    void fetchAccounts();
    return () => accountsController.current?.abort();
  }, [fetchAccounts]);

  const pageController = useRef<AbortController | null>(null);
  const fetchPage = useCallback(async (id: number, number: number) => {
    pageController.current?.abort();
    const controller = new AbortController();
    pageController.current = controller;
    setPage({ status: "loading" });
    try {
      const result = await API.listProviderAssets(id, { page: number, pageSize: PAGE_SIZE }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setPage({ status: "ready", page: result });
    } catch (error) {
      if (controller.signal.aborted) return;
      setPage({ status: "error", error });
    }
  }, []);

  useEffect(() => {
    if (accountId === null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 换账号或翻页后拉取该页素材，旧请求随之作废
    void fetchPage(accountId, pageNumber);
    return () => pageController.current?.abort();
  }, [accountId, pageNumber, fetchPage]);

  const handleSelect = async (asset: ProviderAsset) => {
    if (accountId === null || importingId !== null || target.checkBusy()) return;
    setImportingId(asset.id);
    setImportError(null);
    onImportingChange(true);
    let result: ProviderAssetImportResult;
    try {
      result = await API.importProviderAsset(target.projectName, {
        resource_type: target.resourceType,
        resource_id: target.resourceId,
        account_id: accountId,
        asset_id: asset.id,
      });
    } catch (error) {
      // 导入失败时原选中版本不变，错误留在对话框里，可以换一张素材重试
      setImportError(error);
      setImportingId(null);
      onImportingChange(false);
      return;
    }
    try {
      if ((await target.onImported(result)) === "success") {
        useAppStore.getState().pushToast(t("provider_asset_import_done", { version: result.content_version.version }), "success");
      }
    } finally {
      onImportingChange(false);
      onDone();
    }
  };

  if (accounts.status === "loading") {
    return (
      <DialogBody>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </DialogBody>
    );
  }
  if (accounts.status === "error") {
    return (
      <DialogBody>
        <RequestErrorNotice error={accounts.error} action={{ label: t("common:retry"), onClick: () => void fetchAccounts() }} />
      </DialogBody>
    );
  }
  if (accounts.accounts.length === 0) {
    return (
      <DialogBody>
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-4 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t("provider_asset_no_account")}</p>
          <Button variant="outline" size="sm" render={<Link href={`~${settingsSectionPath("asset-library")}`} />}>
            {t("provider_asset_open_settings")}
          </Button>
        </div>
      </DialogBody>
    );
  }

  const accountItems = accounts.accounts.map((a) => ({
    value: String(a.id),
    label: `${a.name}（${t(siteLabelKey(a.site))}，${a.project_name}）`,
  }));
  const pages = page.status === "ready" ? Math.max(1, Math.ceil(page.page.total / PAGE_SIZE)) : 1;

  return (
    <DialogBody>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <span id={accountLabelId} className="text-sm font-medium">
            {t("provider_asset_account")}
          </span>
          <Select
            items={accountItems}
            value={accountId === null ? null : String(accountId)}
            disabled={importingId !== null}
            onValueChange={(next) => {
              if (next === null) return;
              setAccountId(Number(next));
              setPageNumber(1);
            }}
          >
            <SelectTrigger aria-labelledby={accountLabelId} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {accountItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {importError !== null && <RequestErrorNotice error={importError} />}

        {page.status === "loading" || page.status === "idle" ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
            {t("common:loading")}
          </p>
        ) : page.status === "error" ? (
          <RequestErrorNotice
            error={page.error}
            action={{
              label: t("common:retry"),
              onClick: () => {
                if (accountId !== null) void fetchPage(accountId, pageNumber);
              },
            }}
          />
        ) : page.page.items.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            {t("provider_asset_empty")}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="rounded-lg border border-border">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("provider_asset_col_name")}</TableHead>
                    <TableHead className="w-24">{t("provider_asset_col_type")}</TableHead>
                    <TableHead className="w-40">{t("provider_asset_col_status")}</TableHead>
                    <TableHead className="w-24">
                      <span className="sr-only">{t("provider_asset_col_action")}</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {page.page.items.map((asset) => (
                    <TableRow key={asset.id}>
                      <TableCell>
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <TruncatedText
                            text={asset.name || t("provider_asset_unnamed")}
                            className={cn("text-sm", !asset.name && "text-muted-foreground")}
                          />
                          <TruncatedText text={asset.id} className="font-mono text-xs text-muted-foreground" />
                        </div>
                      </TableCell>
                      <TableCell>
                        {TYPE_LABEL_KEYS[asset.asset_type] ? t(TYPE_LABEL_KEYS[asset.asset_type]) : asset.asset_type}
                      </TableCell>
                      <TableCell>
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <span className={cn("text-sm", STATUS_CLS[asset.status])}>
                            {t(providerAssetStatusLabelKey(asset.status))}
                          </span>
                          {asset.error_code && (
                            <TruncatedText text={asset.error_code} className="font-mono text-xs text-muted-foreground" />
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        {selectable(asset) && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={importingId !== null}
                            aria-label={t("provider_asset_select_named", { name: asset.name || asset.id })}
                            onClick={() => void handleSelect(asset)}
                          >
                            {importingId === asset.id && <Loader2 aria-hidden className="animate-spin" />}
                            {importingId === asset.id ? t("provider_asset_importing") : t("provider_asset_select")}
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
              <span aria-live="polite">
                {t("provider_asset_page_summary", { page: page.page.page, pages, total: page.page.total })}
              </span>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("provider_asset_prev_page")}
                disabled={pageNumber <= 1 || importingId !== null}
                onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
              >
                <ChevronLeft aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("provider_asset_next_page")}
                disabled={pageNumber >= pages || importingId !== null}
                onClick={() => setPageNumber((n) => n + 1)}
              >
                <ChevronRight aria-hidden />
              </Button>
            </div>
          </div>
        )}
      </div>
    </DialogBody>
  );
}
