import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
import { Separator } from "@/components/ui/separator";
import type { AssetLibraryAccountList } from "@/types";

import { AssetLibraryAccountsBlock } from "./AssetLibraryAccountsBlock";
import { AssetLibraryBindingsBlock } from "./AssetLibraryBindingsBlock";
import { ObjectStorageBlock } from "./ObjectStorageBlock";

type LoadState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ready"; data: AssetLibraryAccountList };

/**
 * Global Asset libraries and storage settings: user-owned object storage, provider-library accounts and inference-credential/account bindings.
 * Additions, edits and deletions in every block take effect immediately without the save bar.
 */
export function AssetLibrarySection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [accounts, setAccounts] = useState<LoadState>({ status: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  const loadController = useRef<AbortController | null>(null);
  const fetchAccounts = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const data = await API.listAssetLibraryAccounts({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setAccounts({ status: "ready", data });
    } catch (error) {
      if (controller.signal.aborted) return;
      setAccounts({ status: "error", error });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Fetch library accounts on mount and update state as controlled initialization loading
    void fetchAccounts();
    return () => loadController.current?.abort();
  }, [fetchAccounts]);

  const handleAccountsChanged = useCallback(() => {
    void fetchAccounts();
    setReloadToken((n) => n + 1);
  }, [fetchAccounts]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1">
        <h2 className="text-lg font-medium">{t("settings_asset_library")}</h2>
        <p className="max-w-[40em] text-sm text-muted-foreground">{t("asset_library_section_desc")}</p>
      </div>

      <ObjectStorageBlock />
      <Separator />

      {accounts.status === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      ) : accounts.status === "error" ? (
        <RequestErrorNotice
          error={accounts.error}
          action={{ label: t("common:retry"), onClick: () => void fetchAccounts() }}
        />
      ) : (
        <>
          <AssetLibraryAccountsBlock data={accounts.data} onChanged={handleAccountsChanged} />
          <Separator />
          <AssetLibraryBindingsBlock accounts={accounts.data.accounts} reloadToken={reloadToken} />
        </>
      )}
    </div>
  );
}
