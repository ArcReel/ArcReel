import { useState, useCallback, useMemo } from "react";
import { Loader2, Plus, Trash2, Eye, EyeOff, CheckCircle2, XCircle, Search } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { uid } from "@/utils/id";
import type {
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
} from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ApiFormat = "openai" | "google";
type MediaType = "text" | "image" | "video";

const API_FORMAT_OPTIONS: { value: ApiFormat; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
];

const MEDIA_TYPE_OPTIONS: { value: MediaType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "video", label: "Video" },
];

interface ModelRow {
  key: string; // unique key for React
  model_id: string;
  display_name: string;
  media_type: MediaType;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string;
  price_input: string;
  price_output: string;
  currency: string;
}

function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  return {
    key: uid(),
    model_id: "",
    display_name: "",
    media_type: "text",
    is_default: false,
    is_enabled: true,
    price_unit: "",
    price_input: "",
    price_output: "",
    currency: "USD",
    ...partial,
  };
}

function discoveredToRow(m: DiscoveredModel): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    media_type: m.media_type,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
  });
}

function existingToRow(m: CustomProviderInfo["models"][number]): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    media_type: m.media_type,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    price_unit: m.price_unit ?? "",
    price_input: m.price_input != null ? String(m.price_input) : "",
    price_output: m.price_output != null ? String(m.price_output) : "",
    currency: m.currency ?? "",
  });
}

function rowToInput(r: ModelRow): CustomProviderModelInput {
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    media_type: r.media_type,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.price_unit ? { price_unit: r.price_unit } : {}),
    ...(r.price_input ? { price_input: parseFloat(r.price_input) } : {}),
    ...(r.price_output ? { price_output: parseFloat(r.price_output) } : {}),
    ...(r.currency ? { currency: r.currency } : {}),
  };
}

// ---------------------------------------------------------------------------
// Price label helper
// ---------------------------------------------------------------------------

function priceLabel(mediaType: MediaType): { input: string; output: string } {
  if (mediaType === "video") return { input: "/sec", output: "" };
  if (mediaType === "image") return { input: "/img", output: "" };
  return { input: "/M input", output: "/M output" };
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
  const isEdit = !!existing;

  // --- Form state ---
  const [displayName, setDisplayName] = useState(existing?.display_name ?? "");
  const [apiFormat, setApiFormat] = useState<ApiFormat>(existing?.api_format ?? "openai");
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
      showError("Please enter a Base URL first");
      return;
    }
    if (!apiKey) {
      showError("Please enter an API Key first");
      return;
    }
    setDiscovering(true);
    try {
      const res = await API.discoverModels({ api_format: apiFormat, base_url: baseUrl, api_key: apiKey });
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
      showError(e instanceof Error ? e.message : "Failed to fetch model list");
    } finally {
      setDiscovering(false);
    }
  }, [apiFormat, baseUrl, apiKey, isEdit]);

  // --- Test connection ---
  const handleTest = useCallback(async () => {
    if (!baseUrl) {
      showError("Please enter a Base URL first");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await API.testCustomConnection({ api_format: apiFormat, base_url: baseUrl, api_key: apiKey });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: e instanceof Error ? e.message : "Connection test failed" });
    } finally {
      setTesting(false);
    }
  }, [apiFormat, baseUrl, apiKey]);

  // --- Save ---
  const handleSave = useCallback(async () => {
    // Validation
    if (!displayName.trim()) {
      showError("Please enter a provider name");
      return;
    }
    if (!baseUrl.trim()) {
      showError("Please enter a Base URL");
      return;
    }
    if (!isEdit && !apiKey.trim()) {
      showError("Please enter an API Key");
      return;
    }
    const enabledModels = models.filter((m) => m.is_enabled);
    if (enabledModels.length === 0) {
      showError("At least one model must be enabled");
      return;
    }
    const emptyId = enabledModels.find((m) => !m.model_id.trim());
    if (emptyId) {
      showError("All enabled models must have a model_id");
      return;
    }
    setSaving(true);
    try {
      if (isEdit && existing) {
        // Atomically update provider + models in a single transaction
        await API.fullUpdateCustomProvider(existing.id, {
          display_name: displayName,
          base_url: baseUrl,
          ...(apiKey ? { api_key: apiKey } : {}),
          models: models.map(rowToInput),
        });
      } else {
        await API.createCustomProvider({
          display_name: displayName,
          api_format: apiFormat,
          base_url: baseUrl,
          api_key: apiKey,
          models: models.map(rowToInput),
        });
      }
      onSaved();
    } catch (e) {
      showError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [displayName, apiFormat, baseUrl, apiKey, models, isEdit, existing, onSaved]);

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

  const toggleDefault = (key: string, mediaType: MediaType) => {
    setModels((prev) =>
      prev.map((m) => {
        if (m.media_type !== mediaType) return m;
        return { ...m, is_default: m.key === key ? !m.is_default : false };
      }),
    );
  };

  // --- Shared input classes ---
  const inputCls =
    "w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500";
  const selectCls =
    "rounded-lg border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500";

  // --- Base URL preview (effective models endpoint) ---
  const urlPreview = (() => {
    const trimmed = baseUrl.trim().replace(/\/+$/, "");
    if (!trimmed) return null;
    if (apiFormat === "openai") {
      // OpenAI SDK requires /v1 suffix; the backend fills it in automatically
      const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
      return `${base}/models`;
    }
    // Google SDK appends /v1beta automatically; the backend strips any user-supplied version path
    const base = trimmed.replace(/\/v\d+\w*$/, "");
    return `${base}/v1beta/models`;
  })();

  return (
    <div className="flex h-full flex-col">
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl">
      <h3 className="mb-6 text-lg font-semibold text-gray-100">
        {isEdit ? "Edit Custom Provider" : "Add Custom Provider"}
      </h3>

      <div className="space-y-4">
        {/* Display name */}
        <div>
          <label htmlFor="cp-name" className="mb-1.5 block text-sm text-gray-400">
            Name <span className="text-red-400">*</span>
          </label>
          <input
            id="cp-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g. My NewAPI…"
            className={inputCls}
          />
        </div>

        {/* API Format */}
        <div>
          <label htmlFor="cp-format" className="mb-1.5 block text-sm text-gray-400">
            API Format
          </label>
          <select
            id="cp-format"
            value={apiFormat}
            onChange={(e) => setApiFormat(e.target.value as ApiFormat)}
            disabled={isEdit}
            className={selectCls}
          >
            {API_FORMAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {/* Base URL */}
        <div>
          <label htmlFor="cp-url" className="mb-1.5 block text-sm text-gray-400">
            Base URL <span className="text-red-400">*</span>
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
              Preview: {urlPreview}
            </div>
          )}
        </div>

        {/* API Key */}
        <div>
          <label htmlFor="cp-key" className="mb-1.5 block text-sm text-gray-400">
            API Key {!isEdit && <span className="text-red-400">*</span>}
          </label>
          <div className="relative">
            <input
              id="cp-key"
              type={showApiKey ? "text" : "password"}
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? existing?.api_key_masked ?? "Leave empty to keep existing key" : "Enter API Key"}
              className={`${inputCls} pr-9`}
            />
            <button
              type="button"
              onClick={() => setShowApiKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-gray-500 hover:text-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60"
              aria-label={showApiKey ? "Hide" : "Show"}
            >
              {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
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
                Fetching…
              </>
            ) : (
              "Fetch model list"
            )}
          </button>
        </div>

        {/* Model list */}
        {models.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-3 text-sm text-gray-400">
              <span>Model list</span>
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
                  {filteredModels.every((m) => m.is_enabled) ? "Deselect all" : "Select all"}
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
                  placeholder="Search models…"
                  className="w-full rounded-lg border border-gray-700 bg-gray-900 py-1.5 pl-8 pr-3 text-xs text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            )}
            <div className="space-y-2">
              {filteredModels.map((m) => {
                const pl = priceLabel(m.media_type);
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
                          aria-label="Enable model"
                        />
                      </label>

                      {/* Model ID */}
                      <input
                        type="text"
                        value={m.model_id}
                        onChange={(e) => updateModel(m.key, { model_id: e.target.value })}
                        placeholder="model-id…"
                        aria-label="Model ID"
                        className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
                      />

                      {/* Media type */}
                      <select
                        value={m.media_type}
                        onChange={(e) => updateModel(m.key, { media_type: e.target.value as MediaType })}
                        aria-label="Media type"
                        className={selectCls}
                      >
                        {MEDIA_TYPE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>

                      {/* Default toggle */}
                      <button
                        type="button"
                        onClick={() => toggleDefault(m.key, m.media_type)}
                        className={`rounded-lg px-2 py-1 text-xs transition-colors ${
                          m.is_default
                            ? "bg-indigo-600 text-white"
                            : "border border-gray-700 text-gray-500 hover:border-gray-600 hover:text-gray-300"
                        }`}
                      >
                        Default
                      </button>

                      {/* Remove */}
                      <button
                        type="button"
                        onClick={() => removeModel(m.key)}
                        className="rounded p-1 text-gray-500 hover:text-red-400"
                        aria-label="Remove model"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    {/* Pricing row */}
                    <div className="mt-2 flex flex-wrap items-center gap-2 pl-6 text-xs text-gray-500">
                      <select
                        value={m.currency}
                        onChange={(e) => updateModel(m.key, { currency: e.target.value })}
                        aria-label="Currency"
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
                        aria-label="Input price"
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
                            aria-label="Output price"
                            className="w-16 rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-xs text-gray-300 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
                          />
                          <span>{pl.output}</span>
                        </>
                      )}
                    </div>
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
              Add model manually
            </button>
          </div>
        )}

        {/* Empty model hint */}
        {models.length === 0 && (
          <div className="rounded-xl border border-dashed border-gray-700 p-4 text-center text-sm text-gray-500">
            Click "Fetch model list" to auto-discover, or
            <button
              type="button"
              onClick={addManualModel}
              className="ml-1 text-indigo-400 hover:text-indigo-300"
            >
              add a model manually
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
                Saving…
              </>
            ) : (
              "Save"
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
                Testing…
              </>
            ) : (
              "Test connection"
            )}
          </button>

          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-400 transition-colors hover:text-gray-200"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
