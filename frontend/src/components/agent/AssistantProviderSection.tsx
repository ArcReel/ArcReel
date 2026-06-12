import { useCallback, useEffect, useState } from "react";
import { Bot, Check, Globe, Loader2, Search, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { SectionShell } from "@/components/ui/SectionShell";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

interface ModelPreset {
  id: string;
  name: string;
  provider: string;
}

interface ProviderConfig {
  provider: string;
  litellm_model: string | null;
  litellm_api_key_set: boolean;
  litellm_api_key_masked: string | null;
  litellm_base_url: string | null;
  litellm_max_tool_rounds: number;
  openai_model: string | null;
  openai_api_key_set: boolean;
  openai_api_key_masked: string | null;
  openai_base_url: string | null;
  model_presets: ModelPreset[];
}

export function AssistantProviderSection() {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);

  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // Form state — LiteLLM
  const [provider, setProvider] = useState("claude");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [maxRounds, setMaxRounds] = useState("20");

  // Form state — OpenAI-compatible
  const [openaiModel, setOpenaiModel] = useState("");
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState("");

  // Model discovery
  const [discovering, setDiscovering] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);

  // Load config
  useEffect(() => {
    (async () => {
      try {
        const data = await API.getAssistantProvider();
        setConfig(data);
        setProvider(data.provider);
        setModel(data.litellm_model || "");
        setBaseUrl(data.litellm_base_url || "");
        setMaxRounds(String(data.litellm_max_tool_rounds));
        setOpenaiModel(data.openai_model || "");
        setOpenaiBaseUrl(data.openai_base_url || "");
      } catch {
        // Silent fail
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const patch: Record<string, unknown> = { provider };
      if (provider === "litellm") {
        patch.litellm_model = model;
        if (apiKey) patch.litellm_api_key = apiKey;
        patch.litellm_base_url = baseUrl || null;
        patch.litellm_max_tool_rounds = Number(maxRounds) || 20;
      } else if (provider === "openai") {
        patch.openai_model = openaiModel;
        if (openaiApiKey) patch.openai_api_key = openaiApiKey;
        patch.openai_base_url = openaiBaseUrl || null;
      }
      await API.updateAssistantProvider(patch);
      pushToast(t("settings_saved"), "success");
      // Reload config
      const data = await API.getAssistantProvider();
      setConfig(data);
      setApiKey("");
      setOpenaiApiKey("");
    } catch (e) {
      pushToast(errMsg(e), "error");
    } finally {
      setSaving(false);
    }
  }, [provider, model, apiKey, baseUrl, maxRounds, openaiModel, openaiApiKey, openaiBaseUrl, pushToast, t]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      const testModel = provider === "openai" ? openaiModel : model;
      const testKey = provider === "openai" ? openaiApiKey : apiKey;
      const testUrl = provider === "openai" ? openaiBaseUrl : baseUrl;
      const result = await API.testAssistantProviderConnection({
        model: testModel,
        api_key: testKey || undefined,
        base_url: testUrl || undefined,
      });
      if (result.status === "ok") {
        pushToast(`✅ ${result.response}`, "success");
      } else {
        pushToast(`❌ ${result.error}`, "error");
      }
    } catch (e) {
      pushToast(errMsg(e), "error");
    } finally {
      setTesting(false);
    }
  }, [provider, model, apiKey, baseUrl, openaiModel, openaiApiKey, openaiBaseUrl, pushToast]);

  const handleDiscover = useCallback(async () => {
    const url = provider === "openai" ? openaiBaseUrl : baseUrl;
    const key = provider === "openai" ? openaiApiKey : apiKey;
    if (!url) {
      pushToast(t("fill_base_url_first"), "warning");
      return;
    }
    setDiscovering(true);
    try {
      const result = await API.discoverAssistantModels({ base_url: url, api_key: key });
      if (result.status === "ok") {
        setDiscoveredModels(result.models);
        if (result.models.length === 0) {
          pushToast(t("discover_no_models"), "warning");
        } else {
          pushToast(t("discover_models_success", { count: result.models.length }), "success");
        }
      } else {
        pushToast(`❌ ${result.error}`, "error");
      }
    } catch (e) {
      pushToast(errMsg(e), "error");
    } finally {
      setDiscovering(false);
    }
  }, [provider, openaiBaseUrl, baseUrl, openaiApiKey, apiKey, pushToast, t]);

  if (loading) {
    return (
      <SectionShell kicker="AI" title={t("assistant_provider")}>
        <div className="flex items-center gap-2 text-zinc-400">
          <Loader2 className="size-4 animate-spin" />
          {t("loading")}
        </div>
      </SectionShell>
    );
  }

  const currentApiKeySet = provider === "openai" ? config?.openai_api_key_set : config?.litellm_api_key_set;
  const currentApiKeyMasked = provider === "openai" ? config?.openai_api_key_masked : config?.litellm_api_key_masked;
  const currentTestModel = provider === "openai" ? openaiModel : model;
  const currentTestKey = provider === "openai" ? openaiApiKey : apiKey;
  const currentTestUrl = provider === "openai" ? openaiBaseUrl : baseUrl;

  return (
    <SectionShell kicker="AI" title={t("assistant_provider")} description={t("anthropic_key_required_desc")}>
      <p className="mb-4 text-sm text-zinc-400">{t("assistant_provider_desc")}</p>

      {/* Provider selector */}
      <div className="mb-4 space-y-2">
        <FieldLabel>{t("provider")}</FieldLabel>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setProvider("claude")}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              provider === "claude"
                ? "bg-amber-600/20 text-amber-300 ring-1 ring-amber-500/40"
                : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
            }`}
          >
            <Bot className="size-4" />
            Claude Agent SDK
          </button>
          <button
            type="button"
            onClick={() => setProvider("litellm")}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              provider === "litellm"
                ? "bg-blue-600/20 text-blue-300 ring-1 ring-blue-500/40"
                : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
            }`}
          >
            <Zap className="size-4" />
            LiteLLM (100+ LLMs)
          </button>
          <button
            type="button"
            onClick={() => setProvider("openai")}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              provider === "openai"
                ? "bg-green-600/20 text-green-300 ring-1 ring-green-500/40"
                : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
            }`}
          >
            <Globe className="size-4" />
            {t("openai_compatible")}
          </button>
        </div>
      </div>

      {/* LiteLLM settings */}
      {provider === "litellm" && (
        <div className="space-y-4 rounded-lg border border-zinc-700/50 bg-zinc-900/50 p-4">
          {/* Model selector */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <FieldLabel>{t("model")}</FieldLabel>
              <button
                type="button"
                onClick={() => void handleDiscover()}
                disabled={discovering}
                className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-400 transition-colors hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {discovering ? (
                  <Loader2 className="h-3 w-3 motion-safe:animate-spin" />
                ) : (
                  <Search className="h-3 w-3" />
                )}
                {discovering ? t("discovering_models") : t("discover_models")}
              </button>
            </div>
            <ModelCombobox
              value={model}
              onChange={setModel}
              options={discoveredModels.length > 0 ? discoveredModels : config?.model_presets.map((p) => p.id) || []}
              placeholder="openai/gpt-4o"
              clearable
            />
          </div>

          {/* API Key */}
          <div className="space-y-2">
            <FieldLabel>API Key</FieldLabel>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={config?.litellm_api_key_set ? config.litellm_api_key_masked || "***" : "sk-..."}
              className={INPUT_CLS}
            />
            {config?.litellm_api_key_set && !apiKey && (
              <p className="text-xs text-zinc-500">
                <Check className="mr-1 inline size-3 text-green-400" />
                {t("api_key_configured")}
              </p>
            )}
          </div>

          {/* Base URL */}
          <div className="space-y-2">
            <FieldLabel>{t("base_url_optional")}</FieldLabel>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className={INPUT_CLS}
            />
          </div>

          {/* Max tool rounds */}
          <div className="space-y-2">
            <FieldLabel>{t("max_tool_rounds")}</FieldLabel>
            <input
              type="number"
              value={maxRounds}
              onChange={(e) => setMaxRounds(e.target.value)}
              min={1}
              max={50}
              className={INPUT_CLS}
            />
          </div>

          {/* Test connection */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleTest()}
              disabled={testing || !currentTestModel}
              className="flex items-center gap-2 rounded-lg bg-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-600 disabled:opacity-50"
            >
              {testing ? <Loader2 className="size-3 animate-spin" /> : <Zap className="size-3" />}
              {t("test_connection")}
            </button>
          </div>
        </div>
      )}

      {/* OpenAI-compatible settings */}
      {provider === "openai" && (
        <div className="space-y-4 rounded-lg border border-zinc-700/50 bg-zinc-900/50 p-4">
          <p className="text-xs text-zinc-400">{t("openai_compatible_desc")}</p>

          {/* Base URL */}
          <div className="space-y-2">
            <FieldLabel>Base URL</FieldLabel>
            <input
              type="text"
              value={openaiBaseUrl}
              onChange={(e) => setOpenaiBaseUrl(e.target.value)}
              placeholder="http://127.0.0.1:8000"
              className={INPUT_CLS}
            />
          </div>

          {/* API Key */}
          <div className="space-y-2">
            <FieldLabel>API Key</FieldLabel>
            <input
              type="password"
              value={openaiApiKey}
              onChange={(e) => setOpenaiApiKey(e.target.value)}
              placeholder={config?.openai_api_key_set ? config.openai_api_key_masked || "***" : "sk-..."}
              className={INPUT_CLS}
            />
            {config?.openai_api_key_set && !openaiApiKey && (
              <p className="text-xs text-zinc-500">
                <Check className="mr-1 inline size-3 text-green-400" />
                {t("api_key_configured")}
              </p>
            )}
          </div>

          {/* Model selector */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <FieldLabel>{t("model")}</FieldLabel>
              <button
                type="button"
                onClick={() => void handleDiscover()}
                disabled={discovering}
                className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-400 transition-colors hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {discovering ? (
                  <Loader2 className="h-3 w-3 motion-safe:animate-spin" />
                ) : (
                  <Search className="h-3 w-3" />
                )}
                {discovering ? t("discovering_models") : t("discover_models")}
              </button>
            </div>
            <ModelCombobox
              value={openaiModel}
              onChange={setOpenaiModel}
              options={discoveredModels}
              placeholder="gpt-4o"
              clearable
            />
          </div>

          {/* Test connection */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleTest()}
              disabled={testing || !currentTestModel}
              className="flex items-center gap-2 rounded-lg bg-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-600 disabled:opacity-50"
            >
              {testing ? <Loader2 className="size-3 animate-spin" /> : <Zap className="size-3" />}
              {t("test_connection")}
            </button>
          </div>
        </div>
      )}

      {/* Save button */}
      <div className="mt-4">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
        >
          {saving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
          {t("save")}
        </button>
      </div>
    </SectionShell>
  );
}
