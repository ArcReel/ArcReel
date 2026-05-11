
import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg, voidCall } from "@/utils/async";
import {
  AlertTriangle,
  ChevronDown,
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
import type {
  AgentCredential,
  CreateAgentCredentialRequest,
  PresetProvider,
  TestConnectionResponse,
} from "@/types/agent-credential";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { CredentialList } from "@/components/agent/CredentialList";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { TestResultPanel } from "@/components/agent/TestResultPanel";
import { TabSaveFooter } from "./TabSaveFooter";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface AgentDraft {
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
  // 注：anthropic_api_key / anthropic_base_url 现由 /api/v1/agent/credentials 凭证目录管理，
  // 不再走 /system/config patch 路径（Phase 9 凭证迁移；DEPRECATION 标注见 Task 23）
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
    anthropicModel: "",
    haikuModel: "",
    opusModel: "",
    sonnetModel: "",
    subagentModel: "",
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const savedRef = useRef<AgentDraft>({
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
  const [modelRoutingExpanded, setModelRoutingExpanded] = useState(false);
  const [modelCandidates, setModelCandidates] = useState<string[]>([]);
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const discoverAbortRef = useRef<AbortController | null>(null);

  // Phase 9: 凭证目录 UI 状态
  const [credentials, setCredentials] = useState<AgentCredential[]>([]);
  const [presets, setPresets] = useState<PresetProvider[]>([]);
  const [customSentinelId, setCustomSentinelId] = useState("__custom__");
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [busyCredId, setBusyCredId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
  const [testedCredId, setTestedCredId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingCred, setDeletingCred] = useState(false);

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

  const loadCreds = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([
        API.listAgentCredentials(),
        API.listAgentPresetProviders(),
      ]);
      setCredentials(c.credentials);
      setPresets(p.providers);
      setCustomSentinelId(p.custom_sentinel_id);
    } catch (err) {
      // 静默：凭证列表加载失败不阻塞 Section 2/3
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    void loadCreds();
  }, [loadCreds]);

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

    setDiscoverLoading(true);
    const toast = useAppStore.getState().pushToast;
    try {
      // 不再传 base_url / api_key；后端按 active credential 回退（Task 13/14）
      const res = await API.discoverAnthropicModels(
        {},
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
  }, [t]);

  // ---------------- Credential 目录 handlers (Phase 9) ----------------

  const handleCreate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      await API.createAgentCredential(req);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [loadCreds, t],
  );

  const handleActivate = useCallback(
    async (id: number) => {
      setBusyCredId(id);
      try {
        await API.activateAgentCredential(id);
        await loadCreds();
        const c = credentials.find((x) => x.id === id);
        voidCall(useConfigStatusStore.getState().refresh());
        useAppStore
          .getState()
          .pushToast(
            t("cred_activated_toast", { name: c?.display_name ?? "" }),
            "success",
          );
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        setBusyCredId(null);
      }
    },
    [credentials, loadCreds, t],
  );

  const handleTest = useCallback(
    async (id: number) => {
      setBusyCredId(id);
      setTestResult(null);
      setTestedCredId(id);
      try {
        const res = await API.testAgentCredential(id);
        setTestResult(res);
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        setBusyCredId(null);
      }
    },
    [],
  );

  const requestDelete = useCallback((id: number) => {
    setConfirmDeleteId(id);
  }, []);

  const confirmDelete = useCallback(async () => {
    if (confirmDeleteId == null) return;
    setDeletingCred(true);
    try {
      await API.deleteAgentCredential(confirmDeleteId);
      await loadCreds();
      setConfirmDeleteId(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeletingCred(false);
    }
  }, [confirmDeleteId, loadCreds]);

  const handleApplyFix = useCallback(
    async (suggestedUrl: string) => {
      if (testedCredId == null) return;
      try {
        await API.updateAgentCredential(testedCredId, { base_url: suggestedUrl });
        await loadCreds();
        useAppStore.getState().pushToast(t("agent_config_saved"), "success");
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        // 不论成败都清掉过期面板，避免用户重复点失效按钮
        setTestResult(null);
        setTestedCredId(null);
      }
    },
    [testedCredId, loadCreds, t],
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

        {/* Section 1: Credentials list + Add */}
        <Section
          kicker="Credentials"
          title={t("agent_credentials")}
          description={t("anthropic_key_required_desc")}
          trailing={
            <button
              type="button"
              onClick={() => setAddModalOpen(true)}
              className={GHOST_BTN_CLS}
            >
              + {t("add_credential")}
            </button>
          }
        >
          <CredentialList
            credentials={credentials}
            busyId={busyCredId}
            onActivate={(id) => void handleActivate(id)}
            onTest={(id) => void handleTest(id)}
            onDelete={requestDelete}
          />
          {testResult && (
            <TestResultPanel
              originalBaseUrl={
                testedCredId != null
                  ? credentials.find((c) => c.id === testedCredId)?.base_url ?? null
                  : null
              }
              result={testResult}
              onApplyFix={(suggestedUrl) => void handleApplyFix(suggestedUrl)}
            />
          )}
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

      <AddCredentialModal
        open={addModalOpen}
        presets={presets}
        customSentinelId={customSentinelId}
        onSubmit={handleCreate}
        onClose={() => setAddModalOpen(false)}
      />

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title={t("cred_delete_confirm_title")}
        description={t("cred_delete_confirm")}
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        tone="danger"
        loading={deletingCred}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  );
}
