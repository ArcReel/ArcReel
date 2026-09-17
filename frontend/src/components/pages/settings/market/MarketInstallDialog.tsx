import { useEffect, useId, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { ExternalLink, Loader2, Trash2 } from "lucide-react";
import { API } from "@/api";
import type {
  CustomEndpointInfo,
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
import { isRenderableDefinition } from "../endpoints/endpoint-definition-draft";
import { EndpointDuplicateChoices } from "../endpoints/EndpointDuplicateChoices";
import { EndpointReferenceList, endpointReferences } from "../endpoints/EndpointReferenceList";
import { EntryIcon, SourceChip } from "./MarketEntryCard";
import { KICKER_ACCENT_CLS, KICKER_CLS } from "./market-source-status";

interface Preview {
  detail: MarketEntryDetail;
  definition: unknown;
  matches: boolean;
  validation: EndpointValidateResponse;
  endpoints: CustomEndpointInfo[];
}

function displayValue(value: unknown): string {
  return typeof value === "string" ? value : (JSON.stringify(value) ?? "");
}

/** 安装前完整展示来源、校验和凭证去向；安装与卸载由服务端原子执行。 */
export function MarketInstallDialog({
  entry,
  onClose,
  onInstallationChange,
}: {
  entry: MarketEntry;
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
        setPreview({
          detail,
          definition: payload.definition,
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
  const blocked = !preview || !definition || !preview.matches || !!validation?.errors.length || appVersionUnmet;
  const close = () => {
    if (!busy) onClose();
  };
  const openEndpoint = (key: string) =>
    navigate(`${location}?${new URLSearchParams({ section: "endpoints", endpoint: key })}`);
  const goToModel = (reference: EndpointReference) =>
    navigate(
      `${location}?${new URLSearchParams({ section: "providers", custom: String(reference.provider_id), model: reference.model_id })}`,
    );
  const install = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await API.installMarketEntry(entry.source_id, entry.slug, overwriteId ?? undefined);
      setInstalled(result.installation);
      setSuccess(result.endpoint);
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
          <span className={KICKER_ACCENT_CLS}>Install endpoint</span>
          <ModalCloseButton onClick={close} disabled={busy} />
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-4">
          <header>
            <div className="flex items-center gap-3">
              <EntryIcon entry={entry} />
              <div className="min-w-0">
                <h2 id={titleId} className="font-editorial text-[24px] text-text">
                  {entry.name}
                </h2>
                <p className="text-[12px] text-text-3">
                  {entry.author} · v{entry.version}
                </p>
              </div>
              {installed && <span className="text-[12px] text-good">{t("market_installed")}</span>}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <SourceChip
                name={source?.display_name ?? entry.source_display_name}
                kind={source?.kind ?? null}
              />
              {entry.homepage && (
                <a href={entry.homepage} target="_blank" rel="noreferrer" className={GHOST_BTN_CLS}>
                  {t("market_homepage")}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
            </div>
            <p className="mt-2 text-[12.5px] text-text-2">{entry.description}</p>
            {source?.kind === "custom" && (
              <p className="mt-3 rounded-[8px] border border-warn/30 bg-warn/8 p-3 text-[12px] text-text-2">
                {t("market_unreviewed")}
              </p>
            )}
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
                        version: validation?.min_app_version?.required ?? entry.min_app_version,
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
          {success && (
            <div
              role="status"
              className="rounded-[8px] border border-good/30 bg-good/8 p-3 text-[12.5px] text-text-2"
            >
              <p>{t("market_install_success")}</p>
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
            {installed ? (
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
                {t("market_confirm_install")}
              </button>
            )}
          </div>
        </footer>
      </div>
    </GlassModal>
  );
}
