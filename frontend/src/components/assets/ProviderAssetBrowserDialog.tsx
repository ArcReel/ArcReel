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
import type { AssetLibraryAccount, ProviderAsset, ProviderAssetPage } from "@/types";
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

/**
 * 浏览供应商素材库：按所选素材库账号的站点、区域与项目范围分页列出素材的名称、类型与可用状态。
 * 只浏览，不导入；素材库账号在全局设置「素材库与存储」中配置。
 */
export function ProviderAssetBrowserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("provider_asset_dialog_title")}</DialogTitle>
          <DialogDescription>{t("provider_asset_dialog_desc")}</DialogDescription>
        </DialogHeader>
        {open && <BrowserBody />}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common:close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BrowserBody() {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const accountLabelId = useId();
  const [accounts, setAccounts] = useState<AccountsState>({ status: "loading" });
  const [accountId, setAccountId] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [page, setPage] = useState<PageState>({ status: "idle" });

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
                disabled={pageNumber <= 1}
                onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
              >
                <ChevronLeft aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("provider_asset_next_page")}
                disabled={pageNumber >= pages}
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
