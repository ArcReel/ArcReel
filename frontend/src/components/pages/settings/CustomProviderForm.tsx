import { useState, useCallback, useMemo, useEffect } from "react";
import { Loader2, Plus, Trash2, Eye, EyeOff, CheckCircle2, XCircle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { uid } from "@/utils/id";
import { errMsg } from "@/utils/async";
import type {
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointKey,
} from "@/types";
import { priceLabel, urlPreviewFor, toggleDefaultReducer, type DiscoveryFormat } from "./customProviderHelpers";
import { EndpointSelect } from "./EndpointSelect";
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";
import { IMAGE_STANDARD_RESOLUTIONS, VIDEO_STANDARD_RESOLUTIONS } from "@/utils/provider-models";
import {
  compactRangeFormat,
  parseDurationInput,
  DurationParseError,
  type DurationParseErrorCode,
} from "@/utils/duration_format";

import {
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const COMPACT_INPUT_CLS =
  "min-w-0 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 text-[12.5px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

const DISCOVERY_FORMAT_OPTIONS: { value: DiscoveryFormat; labelKey: string }[] = [
  { value: "openai", labelKey: "discovery_format_openai" },
  { value: "google", labelKey: "discovery_format_google" },
];

interface ModelRow {
  key: string; // unique key for React
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string;
  price_input: string;
  price_output: string;
  currency: string;
  resolution: string; // 空串 = null
  supported_durations_text: string; // 用户原始文本，提交前 parse；空串 = 让后端按 preset 兜底
}

function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  return {
    key: uid(),
    model_id: "",
    display_name: "",
    endpoint: "openai-chat",
    is_default: false,
    is_enabled: true,
    price_unit: "",
    price_input: "",
    price_output: "",
    currency: "USD",
    resolution: "",
    supported_durations_text: "",
    ...partial,
  };
}

function discoveredToRow(m: DiscoveredModel): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
  });
}

function existingToRow(m: CustomProviderInfo["models"][number]): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    price_unit: m.price_unit ?? "",
    price_input: m.price_input != null ? String(m.price_input) : "",
    price_output: m.price_output != null ? String(m.price_output) : "",
    currency: m.currency ?? "",
    resolution: m.resolution ?? "",
    supported_durations_text: m.supported_durations ? compactRangeFormat(m.supported_durations) : "",
  });
}

function rowToInput(r: ModelRow): CustomProviderModelInput {
  const trimmed = r.supported_durations_text.trim();
  // 失败时直接抛 DurationParseError；handleSave 在调用前应已通过 validateModelDurations 拦截，
  // 故此处只负责诚实地把字符串转成 list[int] 而不静默降级（避免无效输入被改成 null
  // 后被后端 preset 自动推断覆盖，造成静默数据偏移）
  const supported_durations = trimmed ? parseDurationInput(trimmed) : null;
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.price_unit ? { price_unit: r.price_unit } : {}),
    ...(r.price_input ? { price_input: parseFloat(r.price_input) } : {}),
    ...(r.price_output ? { price_output: parseFloat(r.price_output) } : {}),
    ...(r.currency ? { currency: r.currency } : {}),
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    ...(supported_durations ? { supported_durations } : { supported_durations: null }),
  };
}

// ---------------------------------------------------------------------------
// DurationsInputRow — 视频模型行内的 supported_durations 输入
// ---------------------------------------------------------------------------

const DURATION_ERROR_KEY: Record<DurationParseErrorCode, string> = {
  empty_after_split: "supported_durations_err_empty_after_split",
  non_positive: "supported_durations_err_non_positive",
  exceeds_max: "supported_durations_err_exceeds_max",
  range_too_large: "supported_durations_err_range_too_large",
  range_inverted: "supported_durations_err_range_inverted",
  unparseable: "supported_durations_err_unparseable",
};

function DurationsInputRow({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleChange = (next: string) => {
    onChange(next);
    if (!next.trim()) {
      setErrorMsg(null);
      return;
    }
    try {
      parseDurationInput(next);
      setErrorMsg(null);
    } catch (e) {
      if (e instanceof DurationParseError) {
        setErrorMsg(t(DURATION_ERROR_KEY[e.code], e.params));
      } else {
        setErrorMsg(t(DURATION_ERROR_KEY.unparseable, { seg: "" }));
      }
    }
  };

  return (
    <div className="mt-2 flex flex-col gap-1 pl-6">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3 whitespace-nowrap">
          {t("supported_durations_label")}
        </span>
        <input
          type="text"
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={t("supported_durations_placeholder")}
          aria-label={t("supported_durations_label")}
          className={`${COMPACT_INPUT_CLS} flex-1`}
        />
      </div>
      {errorMsg ? (
        <p className="text-[11px] text-warm-bright">
          {t("supported_durations_invalid", { message: errorMsg })}
        </p>
      ) : (
        <p className="text-[11px] text-text-4">{t("supported_durations_help")}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface CustomProviderFormProps {
  existing?: CustomProviderInfo | null;
  onSaved: () => void;
  onCancel: () => void;
}

export function CustomProviderForm({ existing, onSaved, onCancel }: CustomProviderFormProps) {
  const { t } = useTranslation("dashboard");
  const isEdit = !!existing;

  // Endpoint catalog（后端单一真相源）：mediaType 推断、price/default 互斥分组都从这里读。
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const endpointToImageCapabilities = useEndpointCatalogStore((s) => s.endpointToImageCapabilities);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    void fetchEndpointCatalog();
  }, [fetchEndpointCatalog]);

  // --- Form state ---
  const [displayName, setDisplayName] = useState(existing?.display_name ?? "");
  const [discoveryFormat, setDiscoveryFormat] = useState<DiscoveryFormat>(existing?.discovery_format ?? "openai");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [models, setModels] = useState<ModelRow[]>(
    existing ? existing.models.map(existingToRow) : [],
  );

  // --- Loading / status ---
  const [discovering, setDiscovering] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const showError = useCallback((msg: string) => useAppStore.getState().pushToast(msg, "error"), []);
  const [modelFilter, setModelFilter] = useState("");

  const filteredModels = useMemo(() => {
    if (!modelFilter.trim()) return models;
    const q = modelFilter.toLowerCase();
    return models.filter((m) => m.model_id.toLowerCase().includes(q));
  }, [models, modelFilter]);

  const allFilteredEnabled = useMemo(
    () => filteredModels.length > 0 && filteredModels.every((m) => m.is_enabled),
    [filteredModels],
  );

  // --- Discover models ---
  const handleDiscover = useCallback(async () => {
    if (!baseUrl) {
      showError(t("fill_base_url_first"));
      return;
    }
    // base_url 相对存储值是否变更：变更后必须用 UI 上的新地址 + 新 key 走明文路径，
    // 否则 by-id 端点会用 DB 中的旧 base_url 发现模型，与保存的新地址错位。
    const baseUrlChanged = isEdit && !!existing && baseUrl.trim() !== existing.base_url;
    // 编辑模式下若用户未输入新 key 且 base_url 未变更，则用已存储凭证（by-id 端点）发现模型；
    // 创建模式或 base_url 变更时必须明文 api_key。
    const useStoredCredential = isEdit && !!existing && !apiKey && !baseUrlChanged;
    if (!useStoredCredential && !apiKey) {
      showError(t(baseUrlChanged ? "base_url_changed_reenter_key" : "fill_api_key_first"));
      return;
    }
    setDiscovering(true);
    try {
      const res = useStoredCredential
        ? await API.discoverModelsForProvider(existing.id)
        : await API.discoverModels({ discovery_format: discoveryFormat, base_url: baseUrl, api_key: apiKey });
      const discovered = res.models.map(discoveredToRow);
      setModels((prev) => {
        const existingIds = new Map(prev.map((r) => [r.model_id, r]));
        const merged: ModelRow[] = [];
        for (const d of discovered) {
          const existing = existingIds.get(d.model_id);
          if (existing) {
            merged.push(existing);
            existingIds.delete(d.model_id);
          } else {
            merged.push(d);
          }
        }
        // Keep manually added models that weren't in the discovery response
        for (const r of existingIds.values()) {
          merged.push(r);
        }
        return merged;
      });
      setModelFilter("");
    } catch (e) {
      showError(errMsg(e, t("fetch_models_failed")));
    } finally {
      setDiscovering(false);
    }
  }, [discoveryFormat, baseUrl, apiKey, isEdit, existing, showError, t]);

  // --- Test connection ---
  const handleTest = useCallback(async () => {
    if (!baseUrl) {
      showError(t("fill_base_url_first"));
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await API.testCustomConnection({ discovery_format: discoveryFormat, base_url: baseUrl, api_key: apiKey });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: errMsg(e, t("connection_test_failed")) });
    } finally {
      setTesting(false);
    }
  }, [discoveryFormat, baseUrl, apiKey, showError, t]);

  // --- Save ---
  const handleSave = useCallback(async () => {
    // Validation
    if (!displayName.trim()) {
      showError(t("fill_provider_name"));
      return;
    }
    if (!baseUrl.trim()) {
      showError(t("fill_base_url"));
      return;
    }
    if (!isEdit && !apiKey.trim()) {
      showError(t("fill_api_key"));
      return;
    }
    const enabledModels = models.filter((m) => m.is_enabled);
    if (enabledModels.length === 0) {
      showError(t("enable_one_model"));
      return;
    }
    const emptyId = enabledModels.find((m) => !m.model_id.trim());
    if (emptyId) {
      showError(t("enabled_model_needs_id"));
      return;
    }
    // 在拼装 payload 前显式校验所有行的 supported_durations 格式：失败则阻断保存，
    // 让用户回去修正标红字段；不再让 rowToInput 静默把非法降级为 null
    let payloadModels: CustomProviderModelInput[];
    try {
      payloadModels = models.map(rowToInput);
    } catch (e) {
      if (e instanceof DurationParseError) {
        const msg = t(DURATION_ERROR_KEY[e.code], e.params);
        showError(t("supported_durations_invalid", { message: msg }));
      } else {
        showError(t("save_failed", { message: errMsg(e) }));
      }
      return;
    }
    setSaving(true);
    try {
      if (isEdit && existing) {
        // 单个事务原子更新 provider + models
        await API.fullUpdateCustomProvider(existing.id, {
          display_name: displayName,
          base_url: baseUrl,
          ...(apiKey ? { api_key: apiKey } : {}),
          models: payloadModels,
        });
      } else {
        await API.createCustomProvider({
          display_name: displayName,
          discovery_format: discoveryFormat,
          base_url: baseUrl,
          api_key: apiKey,
          models: payloadModels,
        });
      }
      onSaved();
    } catch (e) {
      showError(t("save_failed", { message: errMsg(e) }));
    } finally {
      setSaving(false);
    }
  }, [displayName, discoveryFormat, baseUrl, apiKey, models, isEdit, existing, onSaved, showError, t]);

  // --- Model row helpers ---
  const updateModel = (key: string, patch: Partial<ModelRow>) => {
    setModels((prev) => prev.map((m) => (m.key === key ? { ...m, ...patch } : m)));
  };

  const removeModel = (key: string) => {
    setModels((prev) => prev.filter((m) => m.key !== key));
  };

  const addManualModel = () => {
    setModels((prev) => [...prev, newModelRow()]);
  };

  // --- Base URL preview (effective models endpoint) ---
  const urlPreview = urlPreviewFor(discoveryFormat, baseUrl);

  return (
    <div>
      {/* Form content */}
      <div className="p-6 pb-24">
      <div className="max-w-2xl">
      <div className="mb-6">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          {isEdit ? "EDIT PROVIDER" : "NEW PROVIDER"}
        </div>
        <h3
          className="font-editorial mt-1"
          style={{
            fontWeight: 400,
            fontSize: 22,
            lineHeight: 1.1,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          {isEdit ? t("edit_custom_provider") : t("add_custom_provider_title")}
        </h3>
      </div>

      <div className="space-y-4">
        {/* Display name */}
        <div>
          <FieldLabel htmlFor="cp-name" required>
            {t("cp_name_label")}
          </FieldLabel>
          <input
            id="cp-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t("cp_name_placeholder")}
            className={INPUT_CLS}
          />
        </div>

        {/* Base URL */}
        <div>
          <FieldLabel htmlFor="cp-url" required>
            {t("base_url")}
          </FieldLabel>
          <input
            id="cp-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.example.com/v1"
            className={INPUT_CLS}
          />
          {urlPreview && (
            <div className="mt-1.5 truncate font-mono text-[10.5px] text-text-4">
              {t("preview_url")}
              {urlPreview}
            </div>
          )}
        </div>

        {/* API Key */}
        <div>
          <FieldLabel htmlFor="cp-key" required={!isEdit}>
            {t("api_key_label")}
          </FieldLabel>
          <div className="relative">
            <input
              id="cp-key"
              type={showApiKey ? "text" : "password"}
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? existing?.api_key_masked ?? t("keep_existing_key_hint") : t("enter_api_key_placeholder")}
              className={`${INPUT_CLS} pr-10`}
            />
            <button
              type="button"
              onClick={() => setShowApiKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-text-4 transition-colors hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              aria-label={showApiKey ? t("common:hide") : t("common:show")}
            >
              {showApiKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {/* Discovery format (de-emphasized) */}
        <div className="flex flex-wrap items-center gap-2">
          <label
            htmlFor="cp-discovery"
            className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
          >
            {t("discovery_format_label")}
          </label>
          <select
            id="cp-discovery"
            value={discoveryFormat}
            onChange={(e) => setDiscoveryFormat(e.target.value as DiscoveryFormat)}
            disabled={isEdit}
            className="rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 text-[11.5px] text-text-2 hover:border-hairline-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
          >
            {DISCOVERY_FORMAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
            ))}
          </select>
          <span className="font-mono text-[10.5px] text-text-4">{t("discovery_format_help")}</span>
        </div>

        {/* Discover button */}
        <div>
          <button
            type="button"
            onClick={() => void handleDiscover()}
            disabled={discovering}
            className={GHOST_BTN_CLS}
          >
            {discovering ? (
              <>
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
                {t("discovering_models")}
              </>
            ) : (
              t("discover_models")
            )}
          </button>
        </div>

        {/* Model list */}
        {models.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-3">
              <span className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
                {t("model_list")}
              </span>
              {models.length > 1 && (
                <button
                  type="button"
                  onClick={() => {
                    const targetKeys = new Set(filteredModels.map((m) => m.key));
                    setModels((prev) =>
                      prev.map((m) => (targetKeys.has(m.key) ? { ...m, is_enabled: !allFilteredEnabled } : m)),
                    );
                  }}
                  className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-3 transition-colors hover:text-accent-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {allFilteredEnabled ? t("deselect_all") : t("select_all")}
                </button>
              )}
            </div>
            {models.length > 5 && (
              <div className="relative mb-2">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-4" />
                <input
                  type="text"
                  value={modelFilter}
                  onChange={(e) => setModelFilter(e.target.value)}
                  placeholder={t("search_models")}
                  className={`${INPUT_CLS} py-1.5 pl-8 pr-3 text-[12px]`}
                />
              </div>
            )}
            <div className="space-y-2">
              {filteredModels.map((m) => {
                const pl = priceLabel(m.endpoint, endpointToMediaType, t);
                const media = endpointToMediaType[m.endpoint];
                return (
                  <div
                    key={m.key}
                    className="rounded-[10px] border border-hairline p-3"
                    style={CARD_STYLE}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Enable toggle */}
                      <label className="flex cursor-pointer items-center gap-1.5">
                        <input
                          type="checkbox"
                          checked={m.is_enabled}
                          onChange={(e) => updateModel(m.key, { is_enabled: e.target.checked })}
                          className="h-3.5 w-3.5 cursor-pointer rounded border-hairline bg-bg-grad-a accent-[var(--color-accent)]"
                          aria-label={t("enable_model")}
                        />
                      </label>

                      {/* Model ID */}
                      <input
                        type="text"
                        value={m.model_id}
                        onChange={(e) => updateModel(m.key, { model_id: e.target.value })}
                        placeholder="model-id…"
                        aria-label={t("model_id_label")}
                        className={`${COMPACT_INPUT_CLS} flex-1`}
                      />

                      {/* Endpoint select (custom dropdown showing real API path) */}
                      <EndpointSelect
                        value={m.endpoint}
                        onChange={(next) => updateModel(m.key, { endpoint: next, is_default: false })}
                        ariaLabel={t("endpoint_label")}
                      />

                      {/* Default toggle */}
                      <button
                        type="button"
                        onClick={() =>
                          setModels((prev) =>
                            toggleDefaultReducer(prev, m.key, endpointToMediaType, endpointToImageCapabilities),
                          )
                        }
                        className="rounded-[6px] px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        style={
                          m.is_default
                            ? {
                                background: "var(--color-accent-dim)",
                                color: "var(--color-accent-2)",
                                border: "1px solid var(--color-accent-soft)",
                                boxShadow: "0 0 12px -6px var(--color-accent-glow)",
                              }
                            : {
                                background: "var(--color-bg-grad-a)",
                                color: "var(--color-text-3)",
                                border: "1px solid var(--color-hairline)",
                              }
                        }
                      >
                        {t("default_label")}
                      </button>

                      {/* Remove */}
                      <button
                        type="button"
                        onClick={() => removeModel(m.key)}
                        className="rounded p-1 text-text-4 transition-colors hover:text-warm-bright focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        aria-label={t("delete_model")}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    {/* Pricing row */}
                    <div className="mt-2 flex flex-wrap items-center gap-2 pl-6 text-[11px] text-text-4">
                      <select
                        value={m.currency}
                        onChange={(e) => updateModel(m.key, { currency: e.target.value })}
                        aria-label={t("currency_label")}
                        className="rounded-[5px] border border-hairline bg-bg-grad-a/55 px-1 py-0.5 text-[11px] text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      >
                        <option value="USD">$</option>
                        <option value="CNY">&yen;</option>
                      </select>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={m.price_input}
                        onChange={(e) => updateModel(m.key, { price_input: e.target.value })}
                        placeholder="0.00"
                        aria-label={t("input_price")}
                        className={`${COMPACT_INPUT_CLS} w-16`}
                      />
                      <span>{pl.input}</span>
                      {pl.output && (
                        <>
                          <span className="text-text-4">|</span>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={m.price_output}
                            onChange={(e) => updateModel(m.key, { price_output: e.target.value })}
                            placeholder="0.00"
                            aria-label={t("output_price")}
                            className={`${COMPACT_INPUT_CLS} w-16`}
                          />
                          <span>{pl.output}</span>
                        </>
                      )}
                    </div>

                    {/* Resolution row */}
                    {media !== "text" && (
                      <div className="mt-2 flex items-center gap-2 pl-6">
                        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3 whitespace-nowrap">
                          {t("resolution_label")}
                        </span>
                        <ResolutionPicker
                          mode="combobox"
                          options={media === "image" ? IMAGE_STANDARD_RESOLUTIONS : VIDEO_STANDARD_RESOLUTIONS}
                          value={m.resolution || null}
                          onChange={(v) => updateModel(m.key, { resolution: v ?? "" })}
                          placeholder={t("resolution_default_placeholder")}
                          aria-label={t("resolution_label")}
                        />
                      </div>
                    )}

                    {/* Supported durations row（仅 video endpoint） */}
                    {media === "video" && (
                      <DurationsInputRow
                        value={m.supported_durations_text}
                        onChange={(v) => updateModel(m.key, { supported_durations_text: v })}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Add manual model */}
            <button
              type="button"
              onClick={addManualModel}
              className="mt-2 flex items-center gap-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-3 transition-colors hover:text-accent-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("add_model_manually")}
            </button>
          </div>
        )}

        {/* Empty model hint */}
        {models.length === 0 && (
          <div className="rounded-[10px] border border-dashed border-hairline-strong bg-bg-grad-a/45 p-4 text-center text-[12.5px] text-text-3">
            {t("discover_or_add_hint")}
            <button
              type="button"
              onClick={addManualModel}
              className="ml-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2 transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {t("add_model_manually")}
            </button>
          </div>
        )}

        {/* Test result */}
        {testResult && (
          <div
            aria-live="polite"
            className="flex items-start gap-2 rounded-[8px] px-3 py-2 text-[12.5px]"
            style={
              testResult.success
                ? {
                    background: "oklch(0.30 0.10 155 / 0.15)",
                    color: "var(--color-good)",
                    border: "1px solid oklch(0.45 0.10 155 / 0.30)",
                  }
                : {
                    background: "var(--color-warm-tint)",
                    color: "var(--color-warm-bright)",
                    border: "1px solid var(--color-warm-ring)",
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
      </div>{/* end max-w-2xl */}
      </div>{/* end form content */}

      {/* Sticky actions bar */}
      <div
        className="sticky bottom-0 z-10 border-t border-hairline px-6 py-3 backdrop-blur"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.65), oklch(0.15 0.010 265 / 0.85))",
        }}
      >
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-[8px] px-4 py-1.5 text-[12.5px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
            style={ACCENT_BUTTON_STYLE}
          >
            {saving ? (
              <>
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
                {t("common:saving")}
              </>
            ) : (
              t("common:save")
            )}
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
                {t("testing_connection")}
              </>
            ) : (
              t("test_connection")
            )}
          </button>

          <button
            type="button"
            onClick={onCancel}
            className="rounded-[8px] px-3 py-1.5 text-[12.5px] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {t("common:cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
