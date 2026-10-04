import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, ChevronRight, Download, ExternalLink, Loader2 } from "lucide-react";
import { API } from "@/api";
import { endpointSettingsPath, providerSettingsPath } from "@/app-routes";
import type {
  CustomEndpointInfo,
  EndpointDefinition,
  EndpointReference,
  EndpointValidateResponse,
  MarketEntry,
  MarketEntryAggregate,
  MarketEntryDetail,
  MarketEntryInstallation,
} from "@/types";
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { errMsg } from "@/utils/async";
import { isDeclarativeDefinition, isRenderableDefinition } from "../endpoints/endpoint-definition-draft";
import { EndpointDuplicateChoices } from "../endpoints/EndpointDuplicateChoices";
import { EndpointReferenceList, endpointReferences } from "../endpoints/EndpointReferenceList";
import { exportEndpointDefinition } from "../endpoints/export-endpoint-definition";
import { MarketInstallBadges } from "./MarketInstallBadges";
import { EntryIcon, SourceChip } from "./MarketEntryCard";
import { MarketEntryRating } from "./MarketEntryRating";
import { MarketEntryStats } from "./MarketEntryStats";

interface Preview {
  detail: MarketEntryDetail;
  definition: unknown;
  /** 安装时带回，确保装上的就是这里展示给用户核对的定义。 */
  digest: string | null;
  matches: boolean;
  validation: EndpointValidateResponse;
  endpoints: CustomEndpointInfo[];
}

function displayValue(value: unknown): string {
  return typeof value === "string" ? value : (JSON.stringify(value) ?? "");
}

function InfoBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border p-3 text-sm text-subtle-foreground">
      <h3 className="font-medium text-foreground">{title}</h3>
      {children}
    </section>
  );
}

/**
 * 安装与更新共用的确认弹窗：先完整展示来源、校验和凭证去向，安装、更新与卸载由服务端原子执行。
 * 可更新时进入更新态，确认后经同一安装接口原地覆盖持有记录的端点；本地改过的定义先提示会被覆盖并可先导出。
 * 卸载会删除端点，先用 AlertDialog 确认。
 * 传入 `official` 时（官方服务开启且条目来自官方市场源）头部显示安装量与评分，并提供评分控件。
 */
export function MarketInstallDialog({
  entry,
  currentEndpointDefinition,
  hasUnsavedEndpointChanges = false,
  official,
  onClose,
  onInstallationChange,
}: {
  entry: MarketEntry;
  currentEndpointDefinition?: EndpointDefinition;
  hasUnsavedEndpointChanges?: boolean;
  official?: { aggregate: MarketEntryAggregate | null; onRated?: () => void };
  onClose: () => void;
  onInstallationChange: (installation: MarketEntryInstallation | null) => void;
}) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [overwriteId, setOverwriteId] = useState<number | null>(null);
  const [installed, setInstalled] = useState(entry.installation);
  const [success, setSuccess] = useState<CustomEndpointInfo | null>(null);
  const [updatedTo, setUpdatedTo] = useState<string | null>(null);
  const [references, setReferences] = useState<EndpointReference[] | null>(null);
  const [confirmingUninstall, setConfirmingUninstall] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const [detail, payload, endpoints] = await Promise.all([
          API.getMarketEntry(entry.source_id, entry.slug, { signal: controller.signal }),
          API.getMarketEntryDefinition(entry.source_id, entry.slug, { signal: controller.signal }),
          API.listCustomEndpoints({ signal: controller.signal }),
        ]);
        const validation = await API.validateCustomEndpoint(payload.definition, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setError(null);
        setPreview({
          detail,
          definition: payload.definition,
          digest: payload.definition_digest,
          matches: payload.entry_matches_definition,
          validation,
          endpoints: endpoints.endpoints,
        });
        setInstalled(detail.entry.installation);
      } catch (e) {
        if (!controller.signal.aborted) setError(errMsg(e));
      }
    })();
    return () => controller.abort();
  }, [entry.source_id, entry.slug, i18n.language]);

  const definition = preview && isRenderableDefinition(preview.definition) ? preview.definition : null;
  const validation = preview?.validation;
  const source = preview?.detail.source;
  const blockedSources = Object.fromEntries(
    (preview?.endpoints ?? []).flatMap((endpoint) =>
      endpoint.installation
        ? [[endpoint.id, endpoint.installation.source_display_name ?? endpoint.installation.source_key]]
        : [],
    ),
  );
  const appVersionUnmet =
    !!preview && (!preview.detail.entry.min_app_version_satisfied || validation?.min_app_version?.satisfied === false);
  const blocked = !preview?.digest || !definition || !preview.matches || !!validation?.errors.length || appVersionUnmet;
  const updating = installed?.state === "update_available";
  // 卡片与条目详情都可能早于定义所属的快照。投影一致时定义 meta 与该快照的条目逐字段相同，
  // 头部取自摘要绑定的这份定义，与信任块同源；否则安装已被拦下，退回条目详情或卡片。
  const shown = preview?.detail.entry ?? entry;
  const header =
    preview?.matches && definition
      ? {
          name: definition.meta.name,
          author: definition.meta.author,
          version: definition.meta.version,
          description: definition.meta.description ?? null,
          homepage: definition.meta.homepage ?? null,
        }
      : shown;
  const marketVersion = header.version;
  // 市场条目恒为声明式定义；导出按钮吃的也是它，非声明式的保存记录在此没有可导出的东西。
  const installedRecord = preview?.endpoints.find((item) => item.id === installed?.endpoint_id)?.definition;
  const installedDefinition =
    currentEndpointDefinition ??
    (installedRecord && isDeclarativeDefinition(installedRecord) ? installedRecord : undefined);
  const close = () => {
    if (!busy) onClose();
  };
  // 从调用端点小节打开时导航不会卸载弹窗，需主动关闭。
  const openEndpoint = (key: string) => {
    navigate(endpointSettingsPath(key));
    onClose();
  };
  const goToModel = (reference: EndpointReference) =>
    navigate(providerSettingsPath({ custom: reference.provider_id, model: reference.model_id }));
  const install = async () => {
    if (!preview?.digest || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await API.installMarketEntry(
        entry.source_id,
        entry.slug,
        preview.digest,
        (updating ? installed.endpoint_id : overwriteId) ?? undefined,
      );
      setInstalled(result.installation);
      setSuccess(result.endpoint);
      setUpdatedTo(updating ? result.installation.installed_version : null);
      onInstallationChange(result.installation);
      await useEndpointCatalogStore.getState().refresh();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const uninstall = async () => {
    if (!installed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await API.deleteCustomEndpoint(installed.endpoint_id);
      await useEndpointCatalogStore.getState().refresh();
      onInstallationChange(null);
      onClose();
    } catch (e) {
      setConfirmingUninstall(false);
      const refs = endpointReferences(e);
      if (refs) setReferences(refs);
      else setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <div className="flex min-w-0 items-start gap-3">
            <EntryIcon entry={shown} />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <DialogTitle>{header.name}</DialogTitle>
                {installed && <MarketInstallBadges state={installed.state} modified={installed.modified} />}
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                <span>
                  {updating
                    ? t("market_update_versions", { installed: installed.installed_version, version: marketVersion })
                    : t("market_entry_byline", { author: header.author, version: marketVersion })}
                </span>
                <SourceChip name={source?.display_name ?? entry.source_display_name} kind={source?.kind ?? null} />
                {header.homepage && (
                  <a
                    href={header.homepage}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                  >
                    {t("market_homepage")}
                    <ExternalLink className="size-3.5" aria-hidden />
                  </a>
                )}
              </div>
            </div>
          </div>
        </DialogHeader>
        <DialogBody>
          <div className="@container flex flex-col gap-4">
            {header.description && <p className="text-sm text-subtle-foreground">{header.description}</p>}
            {official && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                {official.aggregate && <MarketEntryStats aggregate={official.aggregate} />}
                <MarketEntryRating
                  sourceId={entry.source_id}
                  slug={entry.slug}
                  installed={installed !== null}
                  busy={busy}
                  onBusyChange={setBusy}
                  onRated={official.onRated}
                />
              </div>
            )}
            {source?.kind === "custom" && (
              <p className="flex items-start gap-2 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                {t("market_unreviewed")}
              </p>
            )}
            {!preview && !error && (
              <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                {t("common:loading")}
              </p>
            )}
            {preview && (
              <>
                <div className="grid gap-3 @md:grid-cols-2">
                  <InfoBlock title={t("market_section_validation")}>
                    {!preview.matches && <p className="text-warn">{t("market_definition_mismatch")}</p>}
                    {validation?.errors.map((issue) => (
                      <p key={`${issue.path}-${issue.code}`} className="text-warn">
                        {issue.message}
                      </p>
                    ))}
                    {validation?.warnings.map((issue) => (
                      <p key={`${issue.path}-${issue.code}`}>{issue.message}</p>
                    ))}
                    {preview.matches && validation?.errors.length === 0 && validation.warnings.length === 0 && (
                      <p>{t("ce_diagnostics_clean")}</p>
                    )}
                    {appVersionUnmet && (
                      <p className="text-warn">
                        {t("market_requires_app", {
                          version: validation?.min_app_version?.required ?? shown.min_app_version,
                        })}
                      </p>
                    )}
                  </InfoBlock>
                  <InfoBlock title={t("market_section_hints")}>
                    {validation?.hints?.base_url && (
                      <p className="break-all">{t("ce_import_hint_base_url", { url: validation.hints.base_url })}</p>
                    )}
                    {Array.isArray(validation?.hints?.suggested_models) &&
                      validation.hints.suggested_models
                        .filter((model) => model !== null && typeof model === "object")
                        .map((model, index) => <p key={index}>{displayValue(model.label ?? model.id)}</p>)}
                    {!validation?.hints && <p>{t("market_no_hints")}</p>}
                  </InfoBlock>
                </div>
                {definition && (
                  <InfoBlock title={t("market_section_trust")}>
                    <dl className="flex flex-col gap-2">
                      <div className="flex flex-col gap-0.5">
                        <dt>{t("market_submit_url")}</dt>
                        <dd className="font-mono break-all text-foreground">{displayValue(definition.submit.url)}</dd>
                      </div>
                      <div className="flex flex-col gap-0.5">
                        <dt>{t("market_poll_url")}</dt>
                        <dd className="font-mono break-all text-foreground">{displayValue(definition.poll.url)}</dd>
                      </div>
                    </dl>
                    <Collapsible defaultOpen>
                      <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2.5" />}>
                        <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
                        {t("market_auth")}
                      </CollapsibleTrigger>
                      <CollapsibleContent>
                        {/* 自动换行而不是横向滚动：横向滚动区拿不到键盘焦点。 */}
                        <pre className="mt-1 rounded-md bg-muted p-3 font-mono text-xs break-all whitespace-pre-wrap">
                          {JSON.stringify(definition.auth, null, 2)}
                        </pre>
                      </CollapsibleContent>
                    </Collapsible>
                  </InfoBlock>
                )}
                {!installed && validation && (
                  <EndpointDuplicateChoices
                    duplicates={validation.duplicates}
                    disabled={busy || blocked}
                    selection={{ value: overwriteId, onChange: setOverwriteId }}
                    blockedSources={blockedSources}
                  />
                )}
              </>
            )}
            {updating && (installed.modified || hasUnsavedEndpointChanges) && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-warn/10 px-3 py-2">
                <p className="flex items-center gap-2 text-sm text-warn">
                  <AlertTriangle className="size-4 shrink-0" aria-hidden />
                  {t("market_modified_overwrite_warning")}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!installedDefinition}
                  onClick={() => installedDefinition && exportEndpointDefinition(installedDefinition, entry.slug)}
                >
                  <Download data-icon="inline-start" aria-hidden />
                  {t("market_export_current_definition")}
                </Button>
              </div>
            )}
            {success && (
              <div role="status" className="flex flex-col gap-2 rounded-md border border-border p-3 text-sm">
                <p className="flex items-center gap-2 text-foreground">
                  <Check className="size-4 text-good" aria-hidden />
                  {updatedTo === null ? t("market_install_success") : t("market_update_success", { version: updatedTo })}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      navigate(
                        providerSettingsPath({
                          newCustom: { endpoint: success.key, baseUrl: success.definition.meta.hints?.base_url },
                        }),
                      )
                    }
                  >
                    {t("market_create_provider")}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openEndpoint(success.key)}>
                    {t("market_open_endpoint")}
                  </Button>
                </div>
              </div>
            )}
            {references && (
              <div role="alert" className="text-sm text-subtle-foreground">
                <EndpointReferenceList references={references} onNavigateToModel={goToModel} />
              </div>
            )}
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          {installed && (
            <Button variant="destructive" disabled={busy} className="mr-auto" onClick={() => setConfirmingUninstall(true)}>
              {t("market_uninstall")}
            </Button>
          )}
          <Button variant="outline" disabled={busy} onClick={close}>
            {t("common:cancel")}
          </Button>
          {installed && !updating ? (
            <Button disabled={busy} onClick={() => openEndpoint(installed.endpoint_key)}>
              {t("market_open_endpoint")}
            </Button>
          ) : (
            <Button disabled={busy || blocked} onClick={() => void install()}>
              {busy && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
              {updating ? t("market_update_to", { version: marketVersion }) : t("market_confirm_install")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
      <AlertDialog
        open={confirmingUninstall}
        onOpenChange={(next) => {
          if (!next && !busy) setConfirmingUninstall(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("market_uninstall_title", { name: header.name })}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody>
            <AlertDialogDescription>{t("market_uninstall_desc")}</AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={busy} onClick={() => void uninstall()}>
              {busy && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
              {t("market_uninstall")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  );
}
