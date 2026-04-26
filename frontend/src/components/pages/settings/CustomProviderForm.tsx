import { useState, useCallback, useMemo } from "react";
import { Loader2, Plus, Trash2, Eye, EyeOff, CheckCircle2, XCircle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { uid } from "@/utils/id";
import { errMsg } from "@/utils/async";
import type {
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointKey,
  MediaType,
} from "@/types";
import { ENDPOINT_TO_MEDIA_TYPE } from "@/types";
import { priceLabel, urlPreviewFor, toggleDefaultReducer, type DiscoveryFormat } from "./customProviderHelpers";
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";
import { IMAGE_STANDARD_RESOLUTIONS, VIDEO_STANDARD_RESOLUTIONS } from "@/utils/provider-models";

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

const DISCOVERY_FORMAT_OPTIONS: { value: DiscoveryFormat; labelKey: string }[] = [
  { value: "openai", labelKey: "discovery_format_openai" },
  { value: "google", labelKey: "discovery_format_google" },
];

interface EndpointOption {
  value: EndpointKey;
  labelKey: string;
  mediaType: MediaType;
}

const ENDPOINT_OPTIONS: EndpointOption[] = [
  { value: "openai-chat", labelKey: "endpoint_openai_chat_display", mediaType: "text" },
  { value: "gemini-generate", labelKey: "endpoint_gemini_generate_display", mediaType: "text" },
  { value: "openai-images", labelKey: "endpoint_openai_images_display", mediaType: "image" },
  { value: "gemini-image", labelKey: "endpoint_gemini_image_display", mediaType: "image" },
  { value: "openai-video", labelKey: "endpoint_openai_video_display", mediaType: "video" },
  { value: "newapi-video", labelKey: "endpoint_newapi_video_display", mediaType: "video" },
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
  });
}

function rowToInput(r: ModelRow): CustomProviderModelInput {
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
  };
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

  // --- Discover models ---
  const handleDiscover = useCallback(async () => {
    if (!baseUrl) {
      showError(t("fill_base_url_first"));
      return;
    }
    if (!apiKey) {
      showError(t("fill_api_key_first"));
      return;
    }
    setDiscovering(true);
    try {
      const res = await API.discoverModels({ discovery_format: discoveryFormat, base_url: baseUrl, api_key: apiKey });
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
  }, [discoveryFormat, baseUrl, apiKey, showError, t]);

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
    setSaving(true);
    try {
      if (isEdit && existing) {
        // 单个事务原子更新 provider + models
        await API.fullUpdateCustomProvider(existing.id, {
          display_name: displayName,
          base_url: baseUrl,
          ...(apiKey ? { api_key: apiKey } : {}),
          models: models.map(rowToInput),
        });
      } else {
        await API.createCustomProvider({
          display_name: displayName,
          discovery_format: discoveryFormat,
          base_url: baseUrl,
          api_key: apiKey,
          models: models.map(rowToInput),
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
    setModels((prev) =>
      prev.map((m) => {
        if (m.key !== key) return m;
        const updated = { ...m, ...patch };
        return updated;
      }),
    );
  };

  const removeModel = (key: string) => {
    setModels((prev) => prev.filter((m) => m.key !== key));
  };

  const addManualModel = () => {
    setModels((prev) => [...prev, newModelRow()]);
  };

  // --- Shared input classes ---
  const inputCls =
    "w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus-ring";
  const selectCls =
    "rounded-lg border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100 focus:border-indigo-500 focus-ring";

  // --- Base URL preview (effective models endpoint) ---
  const urlPreview = urlPreviewFor(discoveryFormat, baseUrl);

  return (
    <div className="flex h-full flex-col">
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl">
      <h3 className="mb-6 text-lg font-semibold text-gray-100">
        {isEdit ? t("edit_custom_provider") : t("add_custom_provider_title")}
      </h3>

      <div className="space-y-4">
        {/* Display name */}
        <div>
          <label htmlFor="cp-name" className="mb-1.5 block text-sm text-gray-400">
            {t("cp_name_label")} <span className="text-red-400">*</span>
          </label>
          <input
            id="cp-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t("cp_name_placeholder")}
            className={inputCls}
          />
        </div>

        {/* Base URL */}
        <div>
          <label htmlFor="cp-url" className="mb-1.5 block text-sm text-gray-400">
            {t("base_url")} <span className="text-red-400">*</span>
          </label>
          <input
            id="cp-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.example.com/v1"
            className={inputCls}
          />
          {urlPreview && (
            <div className="mt-1 truncate text-xs text-gray-500">
              {t("preview_url")}{urlPreview}
            </div>
          )}
        </div>

        {/* API Key */}
        <div>
          <label htmlFor="cp-key" className="mb-1.5 block text-sm text-gray-400">
            {t("api_key_label")} {!isEdit && <span className="text-red-400">*</span>}
          </label>
          <div className="relative">
            <input
              id="cp-key"
              type={showApiKey ? "text" : "password"}
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? existing?.api_key_masked ?? t("keep_existing_key_hint") : t("enter_api_key_placeholder")}
              className={`${inputCls} pr-9`}
            />
            <button
              type="button"
              onClick={() => setShowApiKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-gray-500 hover:text-gray-300 focus-ring"
              aria-label={showApiKey ? t("common:hide") : t("common:show")}
            >
              {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Discovery format (de-emphasized) */}
        <div className="text-xs text-gray-500">
          <label htmlFor="cp-discovery" className="mr-2">
            {t("discovery_format_label")}：
          </label>
          <select
            id="cp-discovery"
            value={discoveryFormat}
            onChange={(e) => setDiscoveryFormat(e.target.value as DiscoveryFormat)}
            disabled={isEdit}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-xs text-gray-300"
          >
            {DISCOVERY_FORMAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
            ))}
          </select>
          <span className="ml-2 text-gray-600">{t("discovery_format_help")}</span>
        </div>

        {/* Discover button */}
        <div>
          <button
            type="button"
            onClick={() => void handleDiscover()}
            disabled={discovering}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:text-gray-100 disabled:opacity-50"
          >
            {discovering ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
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
            <div className="mb-2 flex items-center gap-3 text-sm text-gray-400">
              <span>{t("model_list")}</span>
              {models.length > 1 && (
                <button
                  type="button"
                  onClick={() => {
                    const targetKeys = new Set(filteredModels.map((m) => m.key));
                    const allEnabled = filteredModels.every((m) => m.is_enabled);
                    setModels((prev) =>
                      prev.map((m) => (targetKeys.has(m.key) ? { ...m, is_enabled: !allEnabled } : m)),
                    );
                  }}
                  className="text-xs text-indigo-400 hover:text-indigo-300"
                >
                  {filteredModels.every((m) => m.is_enabled) ? t("deselect_all") : t("select_all")}
                </button>
              )}
            </div>
            {models.length > 5 && (
              <div className="relative mb-2">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
                <input
                  type="text"
                  value={modelFilter}
                  onChange={(e) => setModelFilter(e.target.value)}
                  placeholder={t("search_models")}
                  className="w-full rounded-lg border border-gray-700 bg-gray-900 py-1.5 pl-8 pr-3 text-xs text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus-ring"
                />
              </div>
            )}
            <div className="space-y-2">
              {filteredModels.map((m) => {
                const pl = priceLabel(m.endpoint, t);
                return (
                  <div
                    key={m.key}
                    className="rounded-xl border border-gray-800 bg-gray-950/40 p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Enable toggle */}
                      <label className="flex cursor-pointer items-center gap-1.5">
                        <input
                          type="checkbox"
                          checked={m.is_enabled}
                          onChange={(e) => updateModel(m.key, { is_enabled: e.target.checked })}
                          className="h-3.5 w-3.5 rounded border-gray-600 bg-gray-800 text-indigo-500 focus:ring-indigo-500"
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
                        className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
                      />

                      {/* Endpoint select (grouped by media type) */}
                      <select
                        value={m.endpoint}
                        onChange={(e) => updateModel(m.key, { endpoint: e.target.value as EndpointKey, is_default: false })}
                        aria-label={t("endpoint_label")}
                        className={selectCls}
                      >
                        <optgroup label={t("endpoint_text_group")}>
                          {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "text").map((o) => (
                            <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                          ))}
                        </optgroup>
                        <optgroup label={t("endpoint_image_group")}>
                          {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "image").map((o) => (
                            <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                          ))}
                        </optgroup>
                        <optgroup label={t("endpoint_video_group")}>
                          {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "video").map((o) => (
                            <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                          ))}
                        </optgroup>
                      </select>

                      {/* Default toggle */}
                      <button
                        type="button"
                        onClick={() => setModels((prev) => toggleDefaultReducer(prev, m.key))}
                        className={`rounded-lg px-2 py-1 text-xs transition-colors ${
                          m.is_default
                            ? "bg-indigo-600 text-white"
                            : "border border-gray-700 text-gray-500 hover:border-gray-600 hover:text-gray-300"
                        }`}
                      >
                        {t("default_label")}
                      </button>

                      {/* Remove */}
                      <button
                        type="button"
                        onClick={() => removeModel(m.key)}
                        className="rounded p-1 text-gray-500 hover:text-red-400"
                        aria-label={t("delete_model")}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    {/* Pricing row */}
                    <div className="mt-2 flex flex-wrap items-center gap-2 pl-6 text-xs text-gray-500">
                      <select
                        value={m.currency}
                        onChange={(e) => updateModel(m.key, { currency: e.target.value })}
                        aria-label={t("currency_label")}
                        className="rounded border border-gray-700 bg-gray-900 px-1 py-0.5 text-xs text-gray-300 focus-visible:border-indigo-500 focus-visible:outline-none"
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
                        className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-xs text-gray-300 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
                      />
                      <span>{pl.input}</span>
                      {pl.output && (
                        <>
                          <span className="text-gray-600">|</span>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={m.price_output}
                            onChange={(e) => updateModel(m.key, { price_output: e.target.value })}
                            placeholder="0.00"
                            aria-label={t("output_price")}
                            className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-xs text-gray-300 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
                          />
                          <span>{pl.output}</span>
                        </>
                      )}
                    </div>

                    {/* Resolution row */}
                    {ENDPOINT_TO_MEDIA_TYPE[m.endpoint] !== "text" && (
                      <div className="mt-2 flex items-center gap-2 pl-6">
                        <span className="text-sm text-gray-400 whitespace-nowrap">{t("resolution_label")}</span>
                        <ResolutionPicker
                          mode="combobox"
                          options={ENDPOINT_TO_MEDIA_TYPE[m.endpoint] === "image" ? IMAGE_STANDARD_RESOLUTIONS : VIDEO_STANDARD_RESOLUTIONS}
                          value={m.resolution || null}
                          onChange={(v) => updateModel(m.key, { resolution: v ?? "" })}
                          placeholder={t("resolution_default_placeholder")}
                          aria-label={t("resolution_label")}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Add manual model */}
            <button
              type="button"
              onClick={addManualModel}
              className="mt-2 flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-300"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("add_model_manually")}
            </button>
          </div>
        )}

        {/* Empty model hint */}
        {models.length === 0 && (
          <div className="rounded-xl border border-dashed border-gray-700 p-4 text-center text-sm text-gray-500">
            {t("discover_or_add_hint")}
            <button
              type="button"
              onClick={addManualModel}
              className="ml-1 text-indigo-400 hover:text-indigo-300"
            >
              {t("add_model_manually")}
            </button>
          </div>
        )}

        {/* Test result */}
        {testResult && (
          <div
            aria-live="polite"
            className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
              testResult.success
                ? "border-green-800/50 bg-green-900/20 text-green-400"
                : "border-red-800/50 bg-red-900/20 text-red-400"
            }`}
          >
            {testResult.success ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <span>{testResult.message}</span>
          </div>
        )}

      </div>
      </div>{/* end max-w-2xl */}
      </div>{/* end scrollable content */}

      {/* Fixed actions bar — outside scroll area */}
      <div className="shrink-0 border-t border-gray-800 bg-gray-950 px-6 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
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
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:text-gray-100 disabled:opacity-50"
          >
            {testing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("testing_connection")}
              </>
            ) : (
              t("test_connection")
            )}
          </button>

          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-400 transition-colors hover:text-gray-200"
          >
            {t("common:cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
