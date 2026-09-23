import { useEffect, useId, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, Download, ExternalLink, Loader2, Trash2 } from "lucide-react";
import { API } from "@/api";
import type {
  CustomEndpointInfo,
  EndpointDefinition,
  EndpointReference,
  EndpointValidateResponse,
  MarketEntry,
  MarketEntryDetail,
  MarketEntryInstallation,
} from "@/types";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { errMsg } from "@/utils/async";
import { isDeclarativeDefinition, isRenderableDefinition } from "../endpoints/endpoint-definition-draft";
import { EndpointDuplicateChoices } from "../endpoints/EndpointDuplicateChoices";
import { EndpointReferenceList, endpointReferences } from "../endpoints/EndpointReferenceList";
import { exportEndpointDefinition } from "../endpoints/export-endpoint-definition";
import { MarketInstallBadges } from "./MarketInstallBadges";
import { EntryIcon, SourceChip } from "./MarketEntryCard";
import { KICKER_ACCENT_CLS, KICKER_CLS } from "./market-source-status";

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

/**
 * 安装与更新共用的确认弹窗：先完整展示来源、校验和凭证去向，安装、更新与卸载由服务端原子执行。
 * 可更新时进入更新态，确认后经同一安装接口原地覆盖持有记录的端点；本地改过的定义先提示会被覆盖并可先导出。
 */
export function MarketInstallDialog({
  entry,
  currentEndpointDefinition,
  hasUnsavedEndpointChanges = false,
  onClose,
  onInstallationChange,
}: {
  entry: MarketEntry;
  currentEndpointDefinition?: EndpointDefinition;
  hasUnsavedEndpointChanges?: boolean;
  onClose: () => void;
  onInstallationChange: (installation: MarketEntryInstallation | null) => void;
}) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [location, navigate] = useLocation();
  const titleId = useId();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [overwriteId, setOverwriteId] = useState<number | null>(null);
  const [installed, setInstalled] = useState(entry.installation);
  const [success, setSuccess] = useState<CustomEndpointInfo | null>(null);
  const [updatedTo, setUpdatedTo] = useState<string | null>(null);
  const [references, setReferences] = useState<EndpointReference[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const [detail, payload, endpoints] = await Promise.all([
          API.getMarketEntry(entry.source_id, entry.slug, {
            signal: controller.signal,
          }),
          API.getMarketEntryDefinition(entry.source_id, entry.slug, {
            signal: controller.signal,
          }),
          API.listCustomEndpoints({ signal: controller.signal }),
        ]);
        const validation = await API.validateCustomEndpoint(payload.definition, {
          signal: controller.signal,
        });
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
    navigate(`${location}?${new URLSearchParams({ section: "endpoints", endpoint: key })}`);
    onClose();
  };
  const goToModel = (reference: EndpointReference) =>
    navigate(
      `${location}?${new URLSearchParams({ section: "providers", custom: String(reference.provider_id), model: reference.model_id })}`,
    );
  const install = async () => {
    if (!preview?.digest) return;
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
    if (!installed) return;
    setBusy(true);
    setError(null);
    try {
      await API.deleteCustomEndpoint(installed.endpoint_id);
      await useEndpointCatalogStore.getState().refresh();
      onInstallationChange(null);
      onClose();
    } catch (e) {
      const refs = endpointReferences(e);
      if (refs) setReferences(refs);
      else setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <GlassModal
      open
      onClose={close}
      labelledBy={titleId}
      widthClassName="w-full max-w-2xl"
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
    >
      <div className="flex max-h-[86vh] flex-col">
        <div className="flex items-center justify-between px-6 pt-5">
          <span className={KICKER_ACCENT_CLS}>{updating ? "Update endpoint" : "Install endpoint"}</span>
          <ModalCloseButton onClick={close} disabled={busy} />
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-4">
          <header className="flex items-start gap-3">
            <EntryIcon entry={shown} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id={titleId} className="font-editorial text-[24px] leading-tight text-text">
                  {header.name}
                </h2>
                {installed && <MarketInstallBadges state={installed.state} modified={installed.modified} />}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-text-3">
                <span>{header.author}</span>
                <span aria-hidden>·</span>
                <span>
                  {updating
                    ? t("market_update_versions", { installed: installed.installed_version, version: marketVersion })
                    : `v${marketVersion}`}
                </span>
                <span aria-hidden>·</span>
                <SourceChip
                  name={source?.display_name ?? entry.source_display_name}
                  kind={source?.kind ?? null}
                />
                {header.homepage && (
                  <a
                    href={header.homepage}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-accent-2 hover:underline"
                  >
                    {t("market_homepage")}
                    <ExternalLink className="h-3 w-3" aria-hidden />
                  </a>
                )}
              </div>
              {header.description && <p className="mt-2 text-[12.5px] text-text-2">{header.description}</p>}
              {source?.kind === "custom" && (
                <p className="mt-3 rounded-[8px] border border-warn/30 bg-warn/8 p-3 text-[12px] text-text-2">
                  {t("market_unreviewed")}
                </p>
              )}
            </div>
          </header>
          {!preview && !error && (
            <p role="status" className="flex items-center gap-2 text-text-3">
              <Loader2 className="h-4 w-4 motion-safe:animate-spin" aria-hidden />
              {t("common:loading")}
            </p>
          )}
          {preview && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <section className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 p-3 text-[12px] text-text-2">
                  <h3 className={`${KICKER_CLS} mb-2`}>Validation</h3>
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
                </section>
                <section className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 p-3 text-[12px] text-text-2">
                  <h3 className={`${KICKER_CLS} mb-2`}>Hints</h3>
                  {validation?.hints?.base_url && (
                    <p className="break-all">
                      {t("ce_import_hint_base_url", { url: validation.hints.base_url })}
                    </p>
                  )}
                  {Array.isArray(validation?.hints?.suggested_models) &&
                    validation.hints.suggested_models
                      .filter((model) => model !== null && typeof model === "object")
                      .map((model, index) => <p key={index}>{displayValue(model.label ?? model.id)}</p>)}
                  {!validation?.hints && <p>{t("market_no_hints")}</p>}
                </section>
              </div>
              {definition && (
                <section className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 p-3 text-[12px] text-text-2">
                  <h3 className={`${KICKER_CLS} mb-2`}>Trust</h3>
                  <dl className="space-y-2 [&>div]:sm:grid [&>div]:sm:grid-cols-[auto_1fr] [&>div]:sm:gap-3">
                    <div>
                      <dt>{t("market_submit_url")}</dt>
                      <dd className="break-all font-mono text-text">{displayValue(definition.submit.url)}</dd>
                    </div>
                    <div>
                      <dt>{t("market_poll_url")}</dt>
                      <dd className="break-all font-mono text-text">{displayValue(definition.poll.url)}</dd>
                    </div>
                  </dl>
                  <details open className="mt-3">
                    <summary className="cursor-pointer">{t("market_auth")}</summary>
                    <pre className="mt-2 overflow-x-auto rounded-[6px] bg-bg-grad-a p-3 text-[11px]">
                      {JSON.stringify(definition.auth, null, 2)}
                    </pre>
                  </details>
                </section>
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
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-[8px] border border-warn/35 bg-warn/8 px-3 py-2">
              <p className="flex items-center gap-1.5 text-[12px] text-text-2">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
                {t("market_modified_overwrite_warning")}
              </p>
              <button
                type="button"
                disabled={!installedDefinition}
                className={GHOST_BTN_CLS}
                onClick={() => installedDefinition && exportEndpointDefinition(installedDefinition, entry.slug)}
              >
                <Download className="h-3.5 w-3.5" aria-hidden />
                {t("market_export_current_definition")}
              </button>
            </div>
          )}
          {success && (
            <div
              role="status"
              className="rounded-[8px] border border-good/30 bg-good/8 p-3 text-[12.5px] text-text-2"
            >
              <p className="flex items-center gap-1.5 text-text">
                <Check className="h-3.5 w-3.5 text-good" aria-hidden />
                {updatedTo === null ? t("market_install_success") : t("market_update_success", { version: updatedTo })}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className={GHOST_BTN_CLS}
                  onClick={() => {
                    const params = new URLSearchParams({
                      section: "providers",
                      custom: "new",
                      endpoint: success.key,
                    });
                    if (success.definition.meta.hints?.base_url)
                      params.set("base_url", success.definition.meta.hints.base_url);
                    navigate(`${location}?${params}`);
                  }}
                >
                  {t("market_create_provider")}
                </button>
                <button type="button" className={GHOST_BTN_CLS} onClick={() => openEndpoint(success.key)}>
                  {t("market_open_endpoint")}
                </button>
              </div>
            </div>
          )}
          {references && (
            <div role="alert" className="text-[12px] text-text-2">
              <EndpointReferenceList references={references} onNavigateToModel={goToModel} />
            </div>
          )}
          {error && (
            <p role="alert" className="text-[12px] text-warn">
              {error}
            </p>
          )}
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-hairline-soft px-6 py-3">
          {installed ? (
            <button
              type="button"
              disabled={busy}
              className={`${GHOST_BTN_CLS} text-danger`}
              onClick={() => void uninstall()}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              {t("market_uninstall")}
            </button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button type="button" disabled={busy} className={GHOST_BTN_CLS} onClick={close}>
              {t("common:cancel")}
            </button>
            {installed && !updating ? (
              <button
                type="button"
                disabled={busy}
                className={GHOST_BTN_CLS}
                onClick={() => openEndpoint(installed.endpoint_key)}
              >
                {t("market_open_endpoint")}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || blocked}
                className={ACCENT_BTN_SM_CLS}
                style={ACCENT_BUTTON_STYLE}
                onClick={() => void install()}
              >
                {updating ? t("market_update_to", { version: marketVersion }) : t("market_confirm_install")}
              </button>
            )}
          </div>
        </footer>
      </div>
    </GlassModal>
  );
}
