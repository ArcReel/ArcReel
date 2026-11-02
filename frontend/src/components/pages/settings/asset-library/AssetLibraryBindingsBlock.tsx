import { useCallback, useEffect, useId, useRef, useState } from "react";
import { CircleAlert, CircleCheck, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAppStore } from "@/stores/app-store";
import type { AssetLibraryAccount, AssetLibraryBindableCredential } from "@/types";
import { errMsg } from "@/utils/async";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; credentials: AssetLibraryBindableCredential[] };

const UNBOUND = "unbound";

function credentialKey(c: AssetLibraryBindableCredential): string {
  return `${c.credential_type}-${c.credential_id}`;
}

/**
 * 「推理凭证绑定」块：为每条推理凭证选择它可以使用的素材库账号。选择即确认，立即生效；
 * 推理凭证或账号在确认后有变化时显示「需要重新确认」，确认前不使用该账号的素材。
 */
export function AssetLibraryBindingsBlock({
  accounts,
  reloadToken,
}: {
  accounts: AssetLibraryAccount[];
  /** 账号增删改后递增，触发重新读取绑定。 */
  reloadToken: number;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const headingId = useId();
  const pushToast = useAppStore((s) => s.pushToast);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [pending, setPending] = useState<string | null>(null);

  const loadController = useRef<AbortController | null>(null);
  const fetchBindings = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const { credentials } = await API.listAssetLibraryBindings({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setLoad({ status: "ready", credentials });
    } catch (error) {
      if (controller.signal.aborted) return;
      setLoad({ status: "error", error });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载与账号变化后拉取绑定列表回写，属于受控加载
    void fetchBindings();
    return () => loadController.current?.abort();
  }, [fetchBindings, reloadToken]);

  const apply = async (credential: AssetLibraryBindableCredential, accountId: number | null) => {
    if (pending !== null) return;
    const key = credentialKey(credential);
    setPending(key);
    try {
      if (accountId === null) {
        await API.deleteAssetLibraryBinding(credential.credential_type, credential.credential_id);
      } else {
        await API.confirmAssetLibraryBinding(credential.credential_type, credential.credential_id, accountId);
      }
      await fetchBindings();
    } catch (err) {
      pushToast(errMsg(err), "error");
    } finally {
      setPending((current) => (current === key ? null : current));
    }
  };

  const accountItems = [
    { value: UNBOUND, label: t("asset_library_binding_unbound") },
    ...accounts.map((a) => ({ value: String(a.id), label: a.name })),
  ];

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 id={headingId} className="text-base font-medium">
          {t("asset_library_bindings_title")}
        </h3>
        <p className="text-sm text-muted-foreground">{t("asset_library_bindings_desc")}</p>
      </div>

      {load.status === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      ) : load.status === "error" ? (
        <RequestErrorNotice error={load.error} action={{ label: t("common:retry"), onClick: () => void fetchBindings() }} />
      ) : load.credentials.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {t("asset_library_bindings_empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-border">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead>{t("asset_library_col_credential")}</TableHead>
                <TableHead className="w-48">{t("asset_library_col_account")}</TableHead>
                <TableHead className="w-48">{t("asset_library_col_status")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {load.credentials.map((credential) => {
                const key = credentialKey(credential);
                const busy = pending === key;
                const binding = credential.binding;
                return (
                  <TableRow key={key}>
                    <TableCell>
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <TruncatedText text={credential.name} className="text-sm font-medium" />
                        <span className="text-xs text-muted-foreground">
                          {t(
                            credential.credential_type === "builtin"
                              ? "asset_library_credential_builtin"
                              : "asset_library_credential_custom",
                          )}
                        </span>
                        <TruncatedText text={credential.endpoint} className="font-mono text-xs text-muted-foreground" />
                      </div>
                    </TableCell>
                    <TableCell>
                      <Select
                        items={accountItems}
                        value={binding ? String(binding.account_id) : UNBOUND}
                        disabled={pending !== null || accounts.length === 0}
                        onValueChange={(next) => {
                          if (next === null) return;
                          void apply(credential, next === UNBOUND ? null : Number(next));
                        }}
                      >
                        <SelectTrigger
                          size="sm"
                          className="w-full"
                          aria-label={t("asset_library_binding_account_label", { name: credential.name })}
                        >
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
                    </TableCell>
                    <TableCell>
                      {busy ? (
                        <Loader2 aria-label={t("common:loading")} className="size-4 animate-spin text-primary" />
                      ) : binding?.status === "confirmed" ? (
                        <span className="flex items-center gap-1.5 text-sm text-good">
                          <CircleCheck aria-hidden className="size-4" />
                          {t("asset_library_binding_confirmed")}
                        </span>
                      ) : binding?.status === "needs_confirmation" ? (
                        <div className="flex flex-col items-start gap-1">
                          <span className="flex items-center gap-1.5 text-sm text-warn">
                            <CircleAlert aria-hidden className="size-4" />
                            {t("asset_library_binding_needs_confirmation")}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {t("asset_library_binding_needs_confirmation_hint")}
                          </span>
                          <Button
                            variant="outline"
                            size="xs"
                            disabled={pending !== null}
                            onClick={() => void apply(credential, binding.account_id)}
                          >
                            <RefreshCw aria-hidden data-icon="inline-start" />
                            {t("asset_library_binding_reconfirm")}
                          </Button>
                        </div>
                      ) : (
                        <span className="text-sm text-muted-foreground">{t("asset_library_binding_unbound")}</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      {load.status === "ready" && load.credentials.length > 0 && accounts.length === 0 && (
        <p className="text-xs text-muted-foreground">{t("asset_library_bindings_need_account")}</p>
      )}
    </section>
  );
}
