
import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg, voidCall } from "@/utils/async";
import {
  AlertTriangle,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  Loader2,
  Search,
  SlidersHorizontal,
  Terminal,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponse, SystemConfigPatch } from "@/types";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { Popover } from "@/components/ui/Popover";
import { CARD_STYLE, GHOST_BTN_CLS, ICON_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { TabSaveFooter } from "./TabSaveFooter";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface AgentDraft {
  /** New API key input — empty string means "leave saved key untouched". */
  anthropicKey: string;
  /** In-place edit; empty string means "clear saved value". */
  anthropicBaseUrl: string;
  /** In-place edit; empty string means "clear saved value". */
  anthropicModel: string;
  haikuModel: string;
  opusModel: string;
  sonnetModel: string;
  subagentModel: string;
  cleanupDelaySeconds: string;
  maxConcurrentSessions: string;
}

function buildDraft(data: GetSystemConfigResponse): AgentDraft {
  const s = data.settings;
  return {
    anthropicKey: "",
    anthropicBaseUrl: s.anthropic_base_url ?? "",
    anthropicModel: s.anthropic_model ?? "",
    haikuModel: s.anthropic_default_haiku_model ?? "",
    opusModel: s.anthropic_default_opus_model ?? "",
    sonnetModel: s.anthropic_default_sonnet_model ?? "",
    subagentModel: s.claude_code_subagent_model ?? "",
    cleanupDelaySeconds: String(s.agent_session_cleanup_delay_seconds ?? 300),
    maxConcurrentSessions: String(s.agent_max_concurrent_sessions ?? 5),
  };
}

function deepEqual(a: AgentDraft, b: AgentDraft): boolean {
  return (
    a.anthropicKey === b.anthropicKey &&
    a.anthropicBaseUrl === b.anthropicBaseUrl &&
    a.anthropicModel === b.anthropicModel &&
    a.haikuModel === b.haikuModel &&
    a.opusModel === b.opusModel &&
    a.sonnetModel === b.sonnetModel &&
    a.subagentModel === b.subagentModel &&
    a.cleanupDelaySeconds === b.cleanupDelaySeconds &&
    a.maxConcurrentSessions === b.maxConcurrentSessions
  );
}

function buildPatch(draft: AgentDraft, saved: AgentDraft): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (draft.anthropicKey.trim()) patch.anthropic_api_key = draft.anthropicKey.trim();
  if (draft.anthropicBaseUrl !== saved.anthropicBaseUrl)
    patch.anthropic_base_url = draft.anthropicBaseUrl || "";
  if (draft.anthropicModel !== saved.anthropicModel)
    patch.anthropic_model = draft.anthropicModel || "";
  if (draft.haikuModel !== saved.haikuModel)
    patch.anthropic_default_haiku_model = draft.haikuModel || "";
  if (draft.opusModel !== saved.opusModel)
    patch.anthropic_default_opus_model = draft.opusModel || "";
  if (draft.sonnetModel !== saved.sonnetModel)
    patch.anthropic_default_sonnet_model = draft.sonnetModel || "";
  if (draft.subagentModel !== saved.subagentModel)
    patch.claude_code_subagent_model = draft.subagentModel || "";
  if (draft.cleanupDelaySeconds !== saved.cleanupDelaySeconds)
    patch.agent_session_cleanup_delay_seconds = Number(draft.cleanupDelaySeconds) || 300;
  if (draft.maxConcurrentSessions !== saved.maxConcurrentSessions)
    patch.agent_max_concurrent_sessions = Number(draft.maxConcurrentSessions) || 5;
  return patch;
}

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const INLINE_CLEAR_CLS =
  "ml-1.5 inline-flex items-center rounded-[5px] p-0.5 text-text-4 transition-colors hover:text-warm-bright disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

// Model routing config
const MODEL_ROUTING_FIELDS = [
  {
    key: "haikuModel" as const,
    labelKey: "haiku_model",
    envVar: "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    hintKey: "haiku_desc",
    patchKey: "anthropic_default_haiku_model" as const,
  },
  {
    key: "sonnetModel" as const,
    labelKey: "sonnet_model",
    envVar: "ANTHROPIC_DEFAULT_SONNET_MODEL",
    hintKey: "sonnet_desc",
    patchKey: "anthropic_default_sonnet_model" as const,
  },
  {
    key: "opusModel" as const,
    labelKey: "opus_model",
    envVar: "ANTHROPIC_DEFAULT_OPUS_MODEL",
    hintKey: "opus_desc",
    patchKey: "anthropic_default_opus_model" as const,
  },
  {
    key: "subagentModel" as const,
    labelKey: "subagent_model",
    envVar: "CLAUDE_CODE_SUBAGENT_MODEL",
    hintKey: "subagent_desc",
    patchKey: "claude_code_subagent_model" as const,
  },
] as const;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface SectionShellProps {
  kicker: string;
  title: string;
  description?: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}

function Section({ kicker, title, description, trailing, children }: SectionShellProps) {
  return (
    <section>
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
            {kicker}
          </div>
          <h3 className="mt-1 text-[14.5px] font-medium text-text">{title}</h3>
          {description && (
            <p className="mt-1 text-[12px] leading-[1.55] text-text-3">{description}</p>
          )}
        </div>
        {trailing && <div className="shrink-0">{trailing}</div>}
      </div>
      <div className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface AgentConfigTabProps {
  visible: boolean;
}

export function AgentConfigTab({ visible }: AgentConfigTabProps) {
  const { t } = useTranslation("dashboard");
  const [remoteData, setRemoteData] = useState<GetSystemConfigResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDraft>({
    anthropicKey: "",
    anthropicBaseUrl: "",
    anthropicModel: "",
    haikuModel: "",
    opusModel: "",
    sonnetModel: "",
    subagentModel: "",
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const savedRef = useRef<AgentDraft>({
    anthropicKey: "",
    anthropicBaseUrl: "",
    anthropicModel: "",
    haikuModel: "",
    opusModel: "",
    sonnetModel: "",
    subagentModel: "",
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const [saving, setSaving] = useState(false);
  const [clearingField, setClearingField] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [modelRoutingExpanded, setModelRoutingExpanded] = useState(false);
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [importPickerOpen, setImportPickerOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const importTriggerRef = useRef<HTMLButtonElement>(null);
  const [modelCandidates, setModelCandidates] = useState<string[]>([]);
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const discoverAbortRef = useRef<AbortController | null>(null);

  // Load config on mount
  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await API.getSystemConfig();
      setRemoteData(res);
      const d = buildDraft(res);
      savedRef.current = d;
      setDraft(d);
    } catch (err) {
      setLoadError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await API.listCustomProviders();
        if (!cancelled) {
          setProviders(res.providers.filter((p) => p.api_key_masked));
        }
      } catch {
        // 静默：导入是可选功能，不打断主流程
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () => () => {
      discoverAbortRef.current?.abort();
    },
    [],
  );

  const isDirty = !deepEqual(draft, savedRef.current);
  useWarnUnsaved(isDirty);

  const updateDraft = useCallback(
    <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => {
      setDraft((prev) => ({ ...prev, [key]: value }));
      setSaveError(null);
    },
    [],
  );

  const handleSave = useCallback(async () => {
    const patch = buildPatch(draft, savedRef.current);
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await API.updateSystemConfig(patch);
      setRemoteData(res);
      const newDraft = buildDraft(res);
      savedRef.current = newDraft;
      setDraft(newDraft);
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    } catch (err) {
      setSaveError(errMsg(err));
    } finally {
      setSaving(false);
    }
  }, [draft, t]);

  const handleReset = useCallback(() => {
    setDraft(savedRef.current);
    setSaveError(null);
  }, []);

  const handleClearField = useCallback(
    async (fieldId: string, patch: SystemConfigPatch, label: string) => {
      setClearingField(fieldId);
      try {
        const res = await API.updateSystemConfig(patch);
        setRemoteData(res);
        const nextSavedDraft = buildDraft(res);
        savedRef.current = nextSavedDraft;
        setDraft(nextSavedDraft);
        voidCall(useConfigStatusStore.getState().refresh());
        useAppStore
          .getState()
          .pushToast(`${t(`dashboard:${label}`)} ${t("field_cleared")}`, "success");
      } catch (err) {
        useAppStore.getState().pushToast(t("clear_failed", { message: errMsg(err) }), "error");
      } finally {
        setClearingField(null);
      }
    },
    [t],
  );

  const handleDiscoverModels = useCallback(async () => {
    discoverAbortRef.current?.abort();
    const controller = new AbortController();
    discoverAbortRef.current = controller;

    const apiKey = draft.anthropicKey.trim() || undefined;
    const baseUrl = draft.anthropicBaseUrl.trim() || undefined;

    setDiscoverLoading(true);
    const toast = useAppStore.getState().pushToast;
    try {
      const res = await API.discoverAnthropicModels(
        { base_url: baseUrl, api_key: apiKey },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setModelCandidates(res.models.map((m) => m.model_id));
      if (res.models.length === 0) {
        toast(t("discover_no_models"), "warning");
      } else {
        toast(t("discover_models_success", { count: res.models.length }), "success");
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      toast(errMsg(err), "error");
    } finally {
      if (!controller.signal.aborted) setDiscoverLoading(false);
    }
  }, [draft.anthropicKey, draft.anthropicBaseUrl, t]);

  const handleImportProvider = useCallback(
    async (provider: CustomProviderInfo) => {
      setImporting(true);
      try {
        const cred = await API.getCustomProviderCredentials(provider.id);
        setDraft((prev) => ({
          ...prev,
          anthropicKey: cred.api_key,
          anthropicBaseUrl: cred.base_url,
        }));
        useAppStore
          .getState()
          .pushToast(
            t("import_provider_success", { name: provider.display_name }),
            "success",
          );
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        setImporting(false);
        setImportPickerOpen(false);
      }
    },
    [t],
  );

  const isBusy = saving || clearingField !== null;

  // Loading / error states
  if (loadError) {
    return (
      <div className={visible ? "px-1 py-8" : "hidden"}>
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-[8px] border px-4 py-3 text-[12.5px]"
          style={{
            borderColor: "var(--color-warm-ring)",
            background: "var(--color-warm-tint)",
            color: "var(--color-warm-bright)",
          }}
        >
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("load_failed", { message: loadError })}</span>
        </div>
        <button type="button" onClick={() => void load()} className={`${GHOST_BTN_CLS} mt-3`}>
          <Loader2 className="h-3.5 w-3.5" aria-hidden />
          {t("common:retry")}
        </button>
      </div>
    );
  }

  if (!remoteData) {
    return (
      <div
        className={
          visible
            ? "flex items-center gap-2 px-1 py-12 text-text-3"
            : "hidden"
        }
      >
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  const settings = remoteData.settings;

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-7 pb-0 pt-1">
        {/* Page intro */}
        <div className="flex items-start gap-4">
          <div
            className="shrink-0 rounded-[10px] border border-hairline p-3"
            style={{
              ...CARD_STYLE,
              boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
            }}
          >
            <ClaudeColor size={28} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
              Anthropic Bridge
            </div>
            <h2
              className="font-editorial mt-1"
              style={{
                fontWeight: 400,
                fontSize: 24,
                lineHeight: 1.1,
                letterSpacing: "-0.012em",
                color: "var(--color-text)",
              }}
            >
              {t("arcreel_agent")}
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-[1.55] text-text-3">
              {t("agent_sdk_desc")}
            </p>
            <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2">
              <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-text-4" aria-hidden />
              <p className="text-[11.5px] leading-[1.55] text-text-3">
                {t("claude_code_compat_hint")}
              </p>
            </div>
          </div>
        </div>

        {/* Section 1: API credentials */}
        <Section
          kicker="API Credentials"
          title={t("api_credentials")}
          description={t("anthropic_key_required_desc")}
          trailing={
            <>
              <button
                ref={importTriggerRef}
                type="button"
                onClick={() => setImportPickerOpen((v) => !v)}
                disabled={importing || saving}
                className={GHOST_BTN_CLS}
              >
                {importing ? (
                  <Loader2
                    className="h-3.5 w-3.5 motion-safe:animate-spin"
                    aria-hidden
                  />
                ) : (
                  <Download className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("import_from_provider")}
              </button>
              <Popover
                open={importPickerOpen}
                onClose={() => setImportPickerOpen(false)}
                anchorRef={importTriggerRef}
                width="w-64"
                className="rounded-[8px] border border-hairline py-1 shadow-lg"
              >
                {providers.length === 0 ? (
                  <div className="px-3 py-2 text-[12px] text-text-3">
                    {t("import_no_providers")}
                  </div>
                ) : (
                  providers.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => void handleImportProvider(p)}
                      className="block w-full truncate px-3 py-2 text-left text-[12.5px] text-text-2 transition-colors hover:bg-bg-grad-a hover:text-text"
                    >
                      {p.display_name}
                    </button>
                  ))
                )}
              </Popover>
            </>
          }
        >
          <div className="space-y-4">
            {/* API Key */}
            <div>
              <FieldLabel
                htmlFor="agent-anthropic-key"
                className=""
                trailing={
                  settings.anthropic_api_key.is_set && (
                    <div className="flex items-center font-mono text-[10.5px] tabular-nums text-text-4">
                      <span className="truncate">
                        {t("current_label")}
                        {settings.anthropic_api_key.masked ?? t("encrypted")}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          void handleClearField(
                            "anthropic_api_key",
                            { anthropic_api_key: "" },
                            "anthropic_api_key",
                          )
                        }
                        disabled={isBusy}
                        className={INLINE_CLEAR_CLS}
                        aria-label={t("clear_saved_anthropic_key")}
                      >
                        {clearingField === "anthropic_api_key" ? (
                          <Loader2
                            className="h-3 w-3 motion-safe:animate-spin"
                            aria-hidden
                          />
                        ) : (
                          <X className="h-3 w-3" aria-hidden />
                        )}
                      </button>
                    </div>
                  )
                }
              >
                {t("anthropic_api_key")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">{t("env_anthropic_api_key")}</p>
              <div className="relative mt-2">
                <input
                  id="agent-anthropic-key"
                  type={showKey ? "text" : "password"}
                  value={draft.anthropicKey}
                  onChange={(e) => updateDraft("anthropicKey", e.target.value)}
                  placeholder="sk-ant-…"
                  className={`${INPUT_CLS} pr-10`}
                  autoComplete="off"
                  spellCheck={false}
                  name="anthropic_api_key"
                  disabled={saving}
                />
                {draft.anthropicKey && (
                  <button
                    type="button"
                    onClick={() => updateDraft("anthropicKey", "")}
                    className={`absolute right-8 top-1/2 -translate-y-1/2 ${ICON_BTN_CLS}`}
                    aria-label={t("clear_input")}
                  >
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowKey((v) => !v)}
                  className={`absolute right-2 top-1/2 -translate-y-1/2 ${ICON_BTN_CLS}`}
                  aria-label={showKey ? t("hide_key") : t("show_key")}
                >
                  {showKey ? (
                    <EyeOff className="h-4 w-4" aria-hidden />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden />
                  )}
                </button>
              </div>
            </div>

            {/* Base URL */}
            <div className="border-t border-hairline-soft pt-4">
              <FieldLabel
                htmlFor="agent-base-url"
                className=""
                trailing={
                  settings.anthropic_base_url && (
                    <button
                      type="button"
                      onClick={() =>
                        void handleClearField(
                          "anthropic_base_url",
                          { anthropic_base_url: "" },
                          "api_base_url",
                        )
                      }
                      disabled={isBusy}
                      className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-4 transition-colors hover:text-warm-bright disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={t("clear_saved_base_url")}
                    >
                      {clearingField === "anthropic_base_url" ? (
                        <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
                      ) : (
                        <X className="h-3 w-3" aria-hidden />
                      )}
                      {t("clear_saved")}
                    </button>
                  )
                }
              >
                {t("api_base_url")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">{t("env_anthropic_base_url")}</p>
              <div className="relative mt-2">
                <input
                  id="agent-base-url"
                  value={draft.anthropicBaseUrl}
                  onChange={(e) => updateDraft("anthropicBaseUrl", e.target.value)}
                  placeholder={t("api_base_example")}
                  className={`${INPUT_CLS}${draft.anthropicBaseUrl ? " pr-8" : ""}`}
                  autoComplete="off"
                  spellCheck={false}
                  name="anthropic_base_url"
                  disabled={saving}
                />
                {draft.anthropicBaseUrl && (
                  <button
                    type="button"
                    onClick={() => updateDraft("anthropicBaseUrl", "")}
                    className={`absolute right-2 top-1/2 -translate-y-1/2 ${ICON_BTN_CLS}`}
                    aria-label={t("clear_base_url_input")}
                  >
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
              </div>
            </div>
          </div>
        </Section>

        {/* Section 2: Model Configuration */}
        <Section
          kicker="Model Routing"
          title={t("model_config")}
          description={t("model_config_desc")}
          trailing={
            <button
              type="button"
              onClick={() => void handleDiscoverModels()}
              disabled={discoverLoading}
              className={GHOST_BTN_CLS}
            >
              {discoverLoading ? (
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
              ) : (
                <Search className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("discover_models")}
            </button>
          }
        >
          <FieldLabel
            htmlFor="agent-model"
            className=""
            trailing={
              settings.anthropic_model && (
                <button
                  type="button"
                  onClick={() =>
                    void handleClearField(
                      "anthropic_model",
                      { anthropic_model: "" },
                      "default_model",
                    )
                  }
                  disabled={isBusy}
                  className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-4 transition-colors hover:text-warm-bright disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label={t("clear_saved_model")}
                >
                  {clearingField === "anthropic_model" ? (
                    <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
                  ) : (
                    <X className="h-3 w-3" aria-hidden />
                  )}
                  {t("clear_saved")}
                </button>
              )
            }
          >
            {t("default_model")}
          </FieldLabel>
          <p className="mt-0.5 text-[11.5px] text-text-4">{t("env_anthropic_model")}</p>
          <div className="mt-2">
            <ModelCombobox
              id="agent-model"
              value={draft.anthropicModel}
              onChange={(v) => updateDraft("anthropicModel", v)}
              options={modelCandidates}
              placeholder="claude-3-5-sonnet-20241022"
              name="anthropic_model"
              disabled={saving}
              clearable
              clearAriaLabel={t("clear_model_input")}
            />
          </div>

          {/* Advanced model routing */}
          <details
            open={modelRoutingExpanded}
            onToggle={(e) => setModelRoutingExpanded(e.currentTarget.open)}
            className="mt-4 rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 p-4"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between">
              <span className="inline-flex items-center gap-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
                <SlidersHorizontal className="h-3.5 w-3.5 text-accent-2" aria-hidden />
                {t("advanced_model_routing")}
              </span>
              <span className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-hairline-soft bg-bg-grad-a/55 text-text-3">
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform duration-200 ${
                    modelRoutingExpanded ? "rotate-180 text-accent-2" : ""
                  }`}
                  aria-hidden
                />
              </span>
            </summary>
            <p className="mt-2 text-[11.5px] leading-[1.55] text-text-3">
              {t("model_routing_hint")}
            </p>
            <div className="mt-4 grid gap-4">
              {MODEL_ROUTING_FIELDS.map(({ key, labelKey, envVar, hintKey, patchKey }) => {
                const settingsValue = settings[patchKey];
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-2">
                          {t(`dashboard:${labelKey}`)}
                        </div>
                        <div className="text-[11.5px] text-text-4">
                          {t(`dashboard:${hintKey}`)}
                        </div>
                      </div>
                      {settingsValue && (
                        <button
                          type="button"
                          onClick={() =>
                            void handleClearField(
                              patchKey,
                              { [patchKey]: "" } as SystemConfigPatch,
                              labelKey,
                            )
                          }
                          disabled={isBusy}
                          className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-4 transition-colors hover:text-warm-bright disabled:cursor-not-allowed disabled:opacity-50"
                          aria-label={t("clear_saved_field", {
                            label: t(`dashboard:${labelKey}`),
                          })}
                        >
                          {clearingField === patchKey ? (
                            <Loader2
                              className="h-3 w-3 motion-safe:animate-spin"
                              aria-hidden
                            />
                          ) : (
                            <X className="h-3 w-3" aria-hidden />
                          )}
                          {t("clear")}
                        </button>
                      )}
                    </div>
                    <div className="mt-1.5">
                      <ModelCombobox
                        value={draft[key]}
                        onChange={(v) => updateDraft(key, v)}
                        options={modelCandidates}
                        placeholder={envVar}
                        disabled={saving}
                        aria-label={t(`dashboard:${labelKey}`)}
                        clearable
                        clearAriaLabel={t("clear_field_input", {
                          label: t(`dashboard:${labelKey}`),
                        })}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </details>
        </Section>

        {/* Section 3: Advanced */}
        <Section kicker="Runtime Tuning" title={t("advanced_settings")}>
          <div className="space-y-4">
            <div>
              <FieldLabel htmlFor="agent-cleanup-delay" className="">
                {t("session_cleanup_delay_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">
                {t("session_cleanup_delay_desc")}
              </p>
              <input
                id="agent-cleanup-delay"
                type="number"
                min={10}
                max={3600}
                value={draft.cleanupDelaySeconds}
                onChange={(e) => updateDraft("cleanupDelaySeconds", e.target.value)}
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
            <div>
              <FieldLabel htmlFor="agent-max-sessions" className="">
                {t("max_concurrent_sessions_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">
                {t("max_concurrent_sessions_desc")}
              </p>
              <input
                id="agent-max-sessions"
                type="number"
                min={1}
                max={20}
                value={draft.maxConcurrentSessions}
                onChange={(e) =>
                  updateDraft("maxConcurrentSessions", e.target.value)
                }
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
          </div>
        </Section>
      </div>

      <TabSaveFooter
        isDirty={isDirty}
        saving={saving}
        disabled={clearingField !== null}
        error={saveError}
        onSave={() => void handleSave()}
        onReset={handleReset}
      />
    </div>
  );
}
