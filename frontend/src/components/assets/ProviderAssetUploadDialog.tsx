import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, CloudUpload, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import {
  PROVIDER_ASSET_LOCAL_FAILURES,
  siteLabelKey,
} from "@/components/pages/settings/asset-library/asset-library-labels";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppStore } from "@/stores/app-store";
import type {
  ProviderAssetLink,
  ProviderAssetLinkAccount,
  ProviderAssetLinkPanel,
  ProviderAssetLinkState,
} from "@/types";
import { errMsg } from "@/utils/async";
import { dismissOnEscape, dismissOnFocusLeave, keepFocusInside } from "@/utils/inline-confirm";
import { cn } from "cn";

/** 有入库任务仍在推进时的轮询间隔。 */
export const LINK_POLL_INTERVAL_MS = 4000;

const IN_FLIGHT: ReadonlySet<ProviderAssetLinkState> = new Set(["pending", "submitting", "processing", "uncertain"]);

const STATE_CLS: Record<ProviderAssetLinkState, string> = {
  pending: "text-muted-foreground",
  submitting: "text-muted-foreground",
  processing: "text-muted-foreground",
  active: "text-good",
  failed: "text-destructive",
  uncertain: "text-warn",
};

type PanelState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; panel: ProviderAssetLinkPanel };

export interface ProviderAssetUploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  /** 版本历史里的资源类型，如 `characters`。 */
  resourceType: string;
  resourceId: string;
}

/**
 * 上传到供应商素材库：展示图片当前选中版本在各素材库账号里的关联与入库状态，发起上传、解除关联与刷新。
 * 上传立即返回入库任务，供应商审核期间按状态轮询，关闭对话框不影响后台入库。
 */
export function ProviderAssetUploadDialog({
  open,
  onOpenChange,
  projectName,
  resourceType,
  resourceId,
}: ProviderAssetUploadDialogProps) {
  const { t } = useTranslation(["assets", "common"]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t("provider_asset_upload_title")}</DialogTitle>
          <DialogDescription>{t("provider_asset_upload_desc")}</DialogDescription>
        </DialogHeader>
        {open && <UploadPanel projectName={projectName} resourceType={resourceType} resourceId={resourceId} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common:close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// 范围由服务端按云账号身份、站点、区域与项目计算：账号记录换成另一个云账号的 Access Key 后，旧关联不再属于它
function sameScope(link: ProviderAssetLink, account: ProviderAssetLinkAccount): boolean {
  return link.scope_key === account.scope_key;
}

function UploadPanel({ projectName, resourceType, resourceId }: Omit<ProviderAssetUploadDialogProps, "open" | "onOpenChange">) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const [state, setState] = useState<PanelState>({ status: "loading" });
  const controllerRef = useRef<AbortController | null>(null);

  const fetchPanel = useCallback(
    async (initial: boolean) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      if (initial) setState({ status: "loading" });
      try {
        const panel = await API.getProviderAssetLinks(projectName, resourceType, resourceId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setState({ status: "ready", panel });
      } catch (error) {
        if (controller.signal.aborted) return;
        // 轮询中的失败保留已显示的状态，下一轮再取；首次加载失败才换成错误提示
        setState((current) => initial ? { status: "error", error } : { ...current });
      }
    },
    [projectName, resourceType, resourceId],
  );

  useEffect(() => {
    void fetchPanel(true);
    return () => controllerRef.current?.abort();
  }, [fetchPanel]);

  // 有入库任务仍由后台推进时轮询，全部有定论或停止自动查询后不再请求
  const polling = state.status === "ready" && state.panel.links.some((link) => IN_FLIGHT.has(link.state) && link.polling);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setTimeout(() => void fetchPanel(false), LINK_POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [polling, state, fetchPanel]);

  if (state.status === "loading") {
    return (
      <DialogBody>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </DialogBody>
    );
  }
  if (state.status === "error") {
    return (
      <DialogBody>
        <RequestErrorNotice error={state.error} action={{ label: t("common:retry"), onClick: () => void fetchPanel(true) }} />
      </DialogBody>
    );
  }

  const { panel } = state;
  const settingsLink = (
    <Button variant="outline" size="sm" render={<Link href={`~${settingsSectionPath("asset-library")}`} />}>
      {t("provider_asset_open_settings")}
    </Button>
  );
  if (panel.content_version === null) {
    return (
      <DialogBody>
        <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          {t("provider_asset_upload_no_image")}
        </p>
      </DialogBody>
    );
  }
  if (panel.accounts.length === 0 && panel.links.length === 0) {
    return (
      <DialogBody>
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-4 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t("provider_asset_upload_no_account")}</p>
          {settingsLink}
        </div>
      </DialogBody>
    );
  }

  const orphanLinks = panel.links.filter((link) => !panel.accounts.some((account) => sameScope(link, account)));
  return (
    <DialogBody>
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          {t("provider_asset_upload_version", { version: panel.content_version.version })}
        </p>
        {!panel.object_storage_configured && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2">
            <p className="text-sm">{t("provider_asset_upload_no_storage")}</p>
            {settingsLink}
          </div>
        )}
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {panel.accounts.map((account) => (
            <LinkRow
              key={account.id}
              projectName={projectName}
              resourceType={resourceType}
              resourceId={resourceId}
              title={account.name}
              subtitle={`${t(siteLabelKey(account.site))} · ${account.project_name}`}
              site={account.site}
              accountId={account.id}
              link={panel.links.find((link) => sameScope(link, account)) ?? null}
              canUpload={panel.object_storage_configured}
              onChanged={() => void fetchPanel(false)}
            />
          ))}
          {orphanLinks.map((link) => (
            <LinkRow
              key={`link-${link.id}`}
              projectName={projectName}
              resourceType={resourceType}
              resourceId={resourceId}
              title={t("provider_asset_link_account_changed")}
              subtitle={`${t(siteLabelKey(link.site))} · ${link.provider_project_name}`}
              site={link.site}
              accountId={null}
              link={link}
              canUpload={false}
              onChanged={() => void fetchPanel(false)}
            />
          ))}
        </ul>
      </div>
    </DialogBody>
  );
}

interface LinkRowProps {
  projectName: string;
  resourceType: string;
  resourceId: string;
  title: string;
  subtitle: string;
  site: ProviderAssetLink["site"];
  /** 可以向它上传的账号；账号范围已变的历史关联为 null。 */
  accountId: number | null;
  link: ProviderAssetLink | null;
  canUpload: boolean;
  onChanged: () => void;
}

/** 关联状态的说明；供应商给出的失败原文（错误码与消息）单独返回，折叠在详情里。 */
function linkDetail(
  link: ProviderAssetLink,
  t: (key: string, options?: Record<string, unknown>) => string,
): { summary: string; raw: string | null } | null {
  if (link.state === "failed") {
    if (link.error_code && PROVIDER_ASSET_LOCAL_FAILURES.has(link.error_code)) {
      return { summary: t(`provider_asset_link_failure_${link.error_code}`), raw: null };
    }
    const raw = [link.error_code, link.error_message].filter(Boolean).join(": ");
    return { summary: t("provider_asset_link_failure_provider"), raw: raw || null };
  }
  if (link.state === "uncertain") return { summary: t("provider_asset_link_hint_uncertain"), raw: null };
  if (IN_FLIGHT.has(link.state) && !link.polling) return { summary: t("provider_asset_link_hint_stalled"), raw: null };
  return null;
}

function LinkRow({
  projectName,
  resourceType,
  resourceId,
  title,
  subtitle,
  site,
  accountId,
  link,
  canUpload,
  onChanged,
}: LinkRowProps) {
  const { t } = useTranslation(["assets", "common"]);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      onChanged();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      // react-doctor-disable-next-line react-doctor/no-unowned-async-error-clear -- 在途时按钮禁用，同一行不会有并发请求共享这个标志；错误走提示
      setBusy(false);
    }
  };

  const upload = () => {
    if (accountId === null) return;
    void run(() =>
      API.uploadToProviderAssetLibrary(projectName, {
        resource_type: resourceType,
        resource_id: resourceId,
        account_id: accountId,
      }),
    );
  };

  return (
    <li className="flex flex-col gap-1.5 px-3 py-2.5">
      <LinkSummary title={title} subtitle={subtitle} link={link} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {link === null && accountId !== null && (
          <Button size="sm" disabled={busy || !canUpload} onClick={upload}>
            {busy ? <Loader2 aria-hidden className="animate-spin" /> : <CloudUpload aria-hidden />}
            {t(site === "byteplus" ? "provider_asset_upload_action_byteplus" : "provider_asset_upload_action_volcengine")}
          </Button>
        )}
        {link && IN_FLIGHT.has(link.state) && !link.polling && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => void run(() => API.refreshProviderAssetLink(projectName, link.id))}
          >
            <RefreshCw aria-hidden />
            {t("provider_asset_refresh")}
          </Button>
        )}
        {link && (
          <UnlinkControl busy={busy} onConfirm={() => void run(() => API.unlinkProviderAsset(projectName, link.id))} />
        )}
      </div>
    </li>
  );
}

function LinkSummary({ title, subtitle, link }: { title: string; subtitle: string; link: ProviderAssetLink | null }) {
  const { t } = useTranslation(["assets", "common"]);
  const detail = link ? linkDetail(link, t) : null;
  return (
    <>
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <TruncatedText text={title} className="text-sm font-medium" />
          <TruncatedText text={subtitle} className="text-xs text-muted-foreground" />
        </div>
        {link ? (
          <span className={cn("flex shrink-0 items-center gap-1.5 text-sm", STATE_CLS[link.state])}>
            {IN_FLIGHT.has(link.state) && link.polling && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            {t(`provider_asset_link_state_${link.state}`)}
          </span>
        ) : (
          <span className="shrink-0 text-sm text-muted-foreground">{t("provider_asset_not_linked")}</span>
        )}
      </div>
      {link?.provider_asset_id && (
        <TruncatedText
          text={t("provider_asset_link_asset_id", { id: link.provider_asset_id })}
          className="font-mono text-xs text-muted-foreground"
        />
      )}
      {detail && (
        <p className={cn("text-xs", link?.state === "failed" ? "text-destructive" : "text-muted-foreground")}>
          {detail.summary}
        </p>
      )}
      {detail?.raw && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
            <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
            {t("common:error_details")}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p className="font-mono text-xs wrap-break-word text-muted-foreground" translate="no">
              {detail.raw}
            </p>
          </CollapsibleContent>
        </Collapsible>
      )}
    </>
  );
}

/** 「解除关联」的行内确认：确认态焦点落在「取消」上，取消或 Esc 退出时焦点交还触发按钮，焦点离开确认区域也退出。 */
function UnlinkControl({ busy, onConfirm }: { busy: boolean; onConfirm: () => void }) {
  const { t } = useTranslation(["assets", "common"]);
  const [confirming, setConfirming] = useState(false);
  const unlinkRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef(false);

  useEffect(() => {
    if (confirming) {
      if (!busy) cancelRef.current?.focus();
    } else if (returnFocusRef.current) {
      returnFocusRef.current = false;
      unlinkRef.current?.focus();
    }
  }, [confirming, busy]);
  const dismiss = (returnFocus: boolean) => {
    if (busy) return;
    returnFocusRef.current = returnFocus;
    setConfirming(false);
  };

  if (!confirming) {
    return (
      <Button ref={unlinkRef} variant="outline" size="sm" disabled={busy} onClick={() => setConfirming(true)}>
        {t("provider_asset_unlink")}
      </Button>
    );
  }
  return (
    <div
      role="group"
      aria-label={t("provider_asset_unlink_prompt")}
      className="flex min-w-0 flex-wrap items-center justify-end gap-2"
      onBlur={(event) => dismissOnFocusLeave(event, dismiss)}
      onPointerDown={keepFocusInside}
    >
      <span className="text-xs text-muted-foreground">{t("provider_asset_unlink_prompt")}</span>
      <Button variant="destructive" size="sm" disabled={busy} onKeyDown={(event) => dismissOnEscape(event, dismiss)} onClick={onConfirm}>
        {t("provider_asset_unlink_confirm")}
      </Button>
      <Button
        ref={cancelRef}
        variant="outline"
        size="sm"
        disabled={busy}
        onKeyDown={(event) => dismissOnEscape(event, dismiss)}
        onClick={() => dismiss(true)}
      >
        {t("common:cancel")}
      </Button>
    </div>
  );
}
