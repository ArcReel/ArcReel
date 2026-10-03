import { useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ApiKeyInfo } from "@/types";
import { errMsg } from "@/utils/async";
import { formatDate } from "@/utils/date-format";

import { CreateAccessTokenDialog } from "./CreateAccessTokenDialog";

const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "2-digit", day: "2-digit" };

type LoadState = { status: "loading" } | { status: "error"; message: string } | { status: "ready" };

function isExpired(expiresAt: string | null, now: Date): boolean {
  return expiresAt !== null && new Date(expiresAt) < now;
}

/** 全局设置「访问令牌」：列出 ArcReel 签发的访问令牌，创建与吊销。吊销不可逆，用 AlertDialog 确认。 */
export function AccessTokensSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [tokens, setTokens] = useState<ApiKeyInfo[]>([]);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [createOpen, setCreateOpen] = useState(false);
  const [revoking, setRevoking] = useState<ApiKeyInfo | null>(null);

  const loadController = useRef<AbortController | null>(null);
  const fetchTokens = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    setLoad({ status: "loading" });
    try {
      const listed = await API.listApiKeys({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setTokens(listed);
      setLoad({ status: "ready" });
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoad({ status: "error", message: errMsg(err) });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时拉取令牌列表后回写，属于受控的初始化加载
    void fetchTokens();
    return () => loadController.current?.abort();
  }, [fetchTokens]);

  const openCreate = () => setCreateOpen(true);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-lg font-medium">{t("dashboard:settings_access_tokens")}</h2>
          <p className="max-w-[40em] text-sm text-muted-foreground">{t("dashboard:access_tokens_desc")}</p>
        </div>
        <Button onClick={openCreate} className="shrink-0">
          <Plus aria-hidden data-icon="inline-start" />
          {t("dashboard:access_token_create")}
        </Button>
      </div>

      {load.status === "loading" ? (
        <p role="status" className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          {t("common:loading")}
        </p>
      ) : load.status === "error" ? (
        <div role="alert" className="flex items-center justify-between gap-4 rounded-lg border border-destructive/30 bg-destructive/10 p-4">
          <p className="min-w-0 text-sm text-destructive">
            {t("dashboard:access_token_load_failed", { message: load.message })}
          </p>
          <Button variant="outline" size="sm" onClick={() => void fetchTokens()} className="shrink-0">
            {t("common:retry")}
          </Button>
        </div>
      ) : tokens.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center">
          <KeyRound aria-hidden className="size-5 text-muted-foreground" />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium">{t("dashboard:access_token_empty")}</p>
            <p className="text-sm text-muted-foreground">{t("dashboard:access_token_empty_desc")}</p>
          </div>
        </div>
      ) : (
        <TokenTable tokens={tokens} onRevoke={setRevoking} />
      )}

      <CreateAccessTokenDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(token) => {
          if (loadController.current && load.status === "loading") {
            // 创建发生在首次列表加载期间，重新读取以保留既有令牌和刚创建的令牌。
            void fetchTokens();
          } else {
            setTokens((prev) => [token, ...prev]);
            setLoad({ status: "ready" });
          }
        }}
      />
      <RevokeTokenDialog
        token={revoking}
        onClose={() => setRevoking(null)}
        onRevoked={(id) => setTokens((prev) => prev.filter((token) => token.id !== id))}
      />
    </div>
  );
}

function TokenTable({ tokens, onRevoke }: { tokens: ApiKeyInfo[]; onRevoke: (token: ApiKeyInfo) => void }) {
  const { t, i18n } = useTranslation("dashboard");
  const now = new Date();
  return (
    <div className="rounded-lg border border-border bg-card px-2">
      <Table aria-label={t("access_token_list")} className="table-fixed">
        <colgroup>
          <col />
          <col className="w-28" />
          <col className="w-28" />
          <col className="w-28" />
          <col className="w-28" />
          <col className="w-12" />
        </colgroup>
        <TableHeader>
          <TableRow>
            <TableHead>
              <span className="text-muted-foreground">{t("access_token_col_name")}</span>
            </TableHead>
            <TableHead>
              <span className="text-muted-foreground">{t("access_token_col_prefix")}</span>
            </TableHead>
            <TableHead>
              <span className="text-muted-foreground">{t("access_token_col_created")}</span>
            </TableHead>
            <TableHead>
              <span className="text-muted-foreground">{t("access_token_col_expires")}</span>
            </TableHead>
            <TableHead>
              <span className="text-muted-foreground">{t("access_token_col_last_used")}</span>
            </TableHead>
            <TableHead>
              <span className="sr-only">{t("access_token_col_actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tokens.map((token) => (
            <TableRow key={token.id}>
              <TableCell>
                <TruncatedText text={token.name} className="font-medium" />
              </TableCell>
              <TableCell>
                <TruncatedText text={`${token.key_prefix}****`} className="font-mono text-xs text-muted-foreground" />
              </TableCell>
              <TableCell>
                <span className="text-subtle-foreground tabular-nums">
                  {formatDate(token.created_at, i18n.language, DATE_OPTS)}
                </span>
              </TableCell>
              <TableCell>
                {token.expires_at === null ? (
                  <span className="text-muted-foreground">{t("access_token_permanent")}</span>
                ) : isExpired(token.expires_at, now) ? (
                  <span className="text-warn">{t("access_token_expired")}</span>
                ) : (
                  <span className="text-subtle-foreground tabular-nums">
                    {formatDate(token.expires_at, i18n.language, DATE_OPTS)}
                  </span>
                )}
              </TableCell>
              <TableCell>
                <span className="text-muted-foreground tabular-nums">
                  {formatDate(token.last_used_at, i18n.language, DATE_OPTS, t("access_token_never_used"))}
                </span>
              </TableCell>
              <TableCell>
                <div className="flex justify-end">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => onRevoke(token)}
                    aria-label={t("access_token_revoke_named", { name: token.name })}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function RevokeTokenDialog({
  token,
  onClose,
  onRevoked,
}: {
  token: ApiKeyInfo | null;
  onClose: () => void;
  onRevoked: (id: number) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间 token 已清空，保留最后一次的名称避免标题闪成空白
  const [shown, setShown] = useState<ApiKeyInfo | null>(token);
  if (token && token !== shown) {
    setShown(token);
    setError(null);
  }

  const handleRevoke = async () => {
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await API.deleteApiKey(token.id);
      onRevoked(token.id);
      onClose();
    } catch (err) {
      setError(t("dashboard:access_token_revoke_failed", { message: errMsg(err) }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={token !== null}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:access_token_revoke_title", { name: shown?.name ?? "" })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("dashboard:access_token_revoke_desc")}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void handleRevoke()}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {submitting ? t("dashboard:access_token_revoking") : t("dashboard:access_token_revoke_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
