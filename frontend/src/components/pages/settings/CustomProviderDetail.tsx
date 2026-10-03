import { useState, useEffect, useCallback, type CSSProperties } from "react";
import { Loader2, Pencil, Trash2, CheckCircle2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { errMsg } from "@/utils/async";
import type { CustomProviderInfo } from "@/types";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { formatDurationsLabel } from "@/utils/duration_format";
import { formatDate } from "@/utils/date-format";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";
import { CustomProviderForm } from "./CustomProviderForm";
import { isComfyuiProtocol } from "./customProviderHelpers";

//: 模型发现协议 → 展示名。协议是专有名词，不进 i18n；名录外的取值原样显示大写形态。
const PROTOCOL_LABELS: Record<string, string> = {
  openai: "OpenAI",
  google: "Google",
  comfyui: "ComfyUI",
};

function protocolLabel(format: string): string {
  return PROTOCOL_LABELS[format] ?? format;
}

const MEDIA_LABELS: Record<string, string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
  audio: "media_type_audio",
};

const READY_BADGE_STYLE: CSSProperties = {
  background: "oklch(0.30 0.10 155 / 0.18)",
  color: "var(--good)",
  border: "1px solid oklch(0.45 0.10 155 / 0.40)",
  boxShadow: "0 0 14px -6px oklch(0.55 0.10 155 / 0.50)",
};

const UNCONFIGURED_BADGE_STYLE: CSSProperties = {
  background: "var(--card)",
  color: "var(--muted-foreground)",
  border: "1px solid var(--border)",
};

interface CustomProviderDetailProps {
  providerId: number;
  initialModelId?: string;
  onDeleted: () => void;
  onSaved: () => void;
}

export function CustomProviderDetail({ providerId, initialModelId, onDeleted, onSaved }: CustomProviderDetailProps) {
  const { t, i18n } = useTranslation("dashboard");
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    void fetchEndpointCatalog();
  }, [fetchEndpointCatalog]);
  const [provider, setProvider] = useState<CustomProviderInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const showError = useCallback((msg: string) => useAppStore.getState().pushToast(msg, "error"), []);

  const fetchProvider = useCallback(async () => {
    setLoading(true);
    try {
      const data = await API.getCustomProvider(providerId);
      setProvider(data);
    } finally {
      setLoading(false);
    }
  }, [providerId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- providerId 切换时重置编辑/删除/测试态并重新拉取，是动作驱动重置
    setEditing(Boolean(initialModelId));
    setConfirmDelete(false);
    setTestResult(null);
    void fetchProvider();
  }, [fetchProvider, initialModelId]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await API.deleteCustomProvider(providerId);
      // 该供应商上的模型与能力覆盖随之消失，项目会退回解析别的模型；这同样不落任何项目字段。
      useCapabilitiesStore.getState().invalidate();
      onDeleted();
    } catch (e) {
      showError(t("delete_failed", { message: errMsg(e) }));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [providerId, onDeleted, showError, t]);

  const handleTest = useCallback(async () => {
    if (!provider) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await API.checkCustomConnectivityById(provider.id);
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: errMsg(e, t("connectivity_check_failed")) });
    } finally {
      setTesting(false);
    }
  }, [provider, t]);

  const handleFormSaved = useCallback(() => {
    setEditing(false);
    void fetchProvider();
    onSaved();
  }, [fetchProvider, onSaved]);

  if (loading || !provider) {
    return (
      <div className="flex items-center gap-2 px-1 py-12 text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-primary" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  if (editing) {
    return (
      <CustomProviderForm
        existing={provider}
        focusModelId={initialModelId}
        onSaved={handleFormSaved}
        onCancel={() => setEditing(false)}
      />
    );
  }

  const ready = provider.base_url && provider.api_key_masked;

  return (
    <div>
      <div className="p-6 pb-24">
        <div className="max-w-2xl space-y-6">
          {/* Header */}
          <div className="flex items-start gap-3">
            {/* 自定义 provider 恒用字母徽章，不按名字猜品牌（理由见 CustomProviderSection） */}
            <span
              className="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-sm font-mono text-[11px] font-bold uppercase text-subtle-foreground"
              style={{
                background: "var(--card)",
                border: "1px solid var(--input)",
              }}
            >
              {Array.from(provider.display_name)[0] ?? "?"}
            </span>
            <div className="min-w-0">
              <div className="flex items-center gap-2.5">
                <h3
                  className="font-editorial"
                  style={{
                    fontSize: 22,
                    fontWeight: 400,
                    lineHeight: 1.1,
                    letterSpacing: "-0.012em",
                    color: "var(--foreground)",
                  }}
                >
                  {provider.display_name}
                </h3>
                <span
                  className="rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                  style={ready ? READY_BADGE_STYLE : UNCONFIGURED_BADGE_STYLE}
                >
                  {ready ? t("status_connected") : t("status_unconfigured")}
                </span>
              </div>
              <p className="mt-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
                {protocolLabel(provider.discovery_format).toUpperCase()} ·{" "}
                <span className="normal-case tracking-normal">{provider.base_url}</span>
              </p>
            </div>
          </div>

          {/* Info card */}
          <div className="rounded-lg border border-border p-5" style={CARD_STYLE}>
            <div className="mb-3 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-primary">
              Connection
            </div>
            <div className="space-y-2 text-[12.5px]">
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">{t("discovery_format_label")}</span>
                <span className="text-foreground">{protocolLabel(provider.discovery_format)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">{t("base_url")}</span>
                <span className="truncate font-mono text-[11.5px] text-foreground">{provider.base_url}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">{t("api_key_label")}</span>
                <span className="font-mono text-[11.5px] text-foreground">
                  {provider.api_key_masked || t("api_key_not_set")}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">{t("created_at")}</span>
                <span className="text-foreground">
                  {formatDate(provider.created_at, i18n.language, { year: "numeric", month: "2-digit", day: "2-digit" })}
                </span>
              </div>
            </div>
          </div>

          {/* Models */}
          {provider.models.length > 0 && (
            <div>
              <div className="mb-2 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-primary">
                {t("model_list")}
              </div>
              <div className="space-y-1.5">
                {provider.models.map((m) => (
                  <div
                    key={m.id}
                    className={`flex items-center gap-2 rounded-md border border-border px-3 py-2 text-[12.5px] ${
                      m.is_enabled ? "text-foreground" : "text-muted-foreground"
                    }`}
                    style={CARD_STYLE}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">
                      {m.model_id}
                    </span>
                    <span className="rounded-full border border-border/50 bg-card/55 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
                      {(() => {
                        const media = endpointToMediaType[m.endpoint];
                        return MEDIA_LABELS[media] ? t(MEDIA_LABELS[media]) : media;
                      })()}
                    </span>
                    {m.is_default && (
                      <span
                        className="rounded-full px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                        style={{
                          background: "color-mix(in oklab, var(--primary) 12%, transparent)",
                          color: "var(--primary)",
                          border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
                        }}
                      >
                        {t("default_label")}
                      </span>
                    )}
                    {m.supported_durations && m.supported_durations.length > 0 && (
                      <span className="font-mono text-[10.5px] text-muted-foreground">
                        {t("supported_durations_summary", {
                          value: formatDurationsLabel(m.supported_durations),
                        })}
                      </span>
                    )}
                    {!m.is_enabled && (
                      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                        {t("model_disabled")}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ComfyUI：反向代理用自定义头鉴权时探针本就打不通，提前说明以什么为准 */}
          {isComfyuiProtocol(provider.discovery_format) && (
            <p className="text-[12px] leading-[1.55] text-muted-foreground">{t("cp_comfyui_connectivity_hint")}</p>
          )}

          {/* Test result */}
          {testResult && (
            <div
              aria-live="polite"
              className="flex items-start gap-2 rounded-md px-3 py-2 text-[12px]"
              style={
                testResult.success
                  ? {
                      background: "oklch(0.30 0.10 155 / 0.15)",
                      color: "var(--good)",
                      border: "1px solid oklch(0.45 0.10 155 / 0.30)",
                    }
                  : {
                      background: "color-mix(in oklab, var(--warn) 15%, transparent)",
                      color: "var(--warn)",
                      border: "1px solid color-mix(in oklab, var(--warn) 30%, transparent)",
                    }
              }
            >
              {testResult.success ? (
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              ) : (
                <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              )}
              <span>{testResult.message}</span>
            </div>
          )}
        </div>
      </div>

      {/* Sticky actions bar */}
      <div
        className="sticky bottom-0 z-10 border-t border-border px-6 py-3 backdrop-blur"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.65), oklch(0.15 0.010 265 / 0.85))",
        }}
      >
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setEditing(true)}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            <Pencil className="h-3.5 w-3.5" />
            {t("common:edit")}
          </button>

          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={testing}
            className={GHOST_BTN_CLS}
          >
            {testing ? (
              <>
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
                {t("connectivity_checking")}
              </>
            ) : (
              t("connectivity_check")
            )}
          </button>

          {!confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card/55 px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:border-warn/30 hover:text-warn focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("common:delete")}
            </button>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={deleting}
                className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12.5px] font-semibold transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                style={{
                  background: "color-mix(in oklab, var(--warn) 15%, transparent)",
                  color: "var(--warn)",
                  border: "1px solid color-mix(in oklab, var(--warn) 30%, transparent)",
                }}
              >
                {deleting ? (
                  <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                {t("confirm_delete_provider")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className={GHOST_BTN_CLS}
              >
                {t("common:cancel")}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
