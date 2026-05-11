
import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg, voidCall } from "@/utils/async";
import {
  AlertTriangle,
  Loader2,
  Terminal,
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
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
import { CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { CredentialList } from "@/components/agent/CredentialList";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { TabSaveFooter } from "./TabSaveFooter";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface AgentDraft {
  cleanupDelaySeconds: string;
  maxConcurrentSessions: string;
}

function buildDraft(data: GetSystemConfigResponse): AgentDraft {
  const s = data.settings;
  return {
    cleanupDelaySeconds: String(s.agent_session_cleanup_delay_seconds ?? 300),
    maxConcurrentSessions: String(s.agent_max_concurrent_sessions ?? 5),
  };
}

function deepEqual(a: AgentDraft, b: AgentDraft): boolean {
  return (
    a.cleanupDelaySeconds === b.cleanupDelaySeconds &&
    a.maxConcurrentSessions === b.maxConcurrentSessions
  );
}

function buildPatch(draft: AgentDraft, saved: AgentDraft): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (draft.cleanupDelaySeconds !== saved.cleanupDelaySeconds)
    patch.agent_session_cleanup_delay_seconds = Number(draft.cleanupDelaySeconds) || 300;
  if (draft.maxConcurrentSessions !== saved.maxConcurrentSessions)
    patch.agent_max_concurrent_sessions = Number(draft.maxConcurrentSessions) || 5;
  return patch;
}

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
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const savedRef = useRef<AgentDraft>({
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 凭证目录 UI 状态
  const [credentials, setCredentials] = useState<AgentCredential[]>([]);
  const [presets, setPresets] = useState<PresetProvider[]>([]);
  const [customSentinelId, setCustomSentinelId] = useState("__custom__");
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [busyCredId, setBusyCredId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
  const [testedCredId, setTestedCredId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingCred, setDeletingCred] = useState(false);
  const [editingCred, setEditingCred] = useState<AgentCredential | null>(null);

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
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    void loadCreds();
  }, [loadCreds]);

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

  // ---------------- Credential 目录 handlers ----------------

  const handleCreate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      await API.createAgentCredential(req);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [loadCreds, t],
  );

  const handleEdit = useCallback((c: AgentCredential) => {
    setEditingCred(c);
  }, []);

  const handleUpdate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      if (editingCred == null) return;
      const patch: UpdateAgentCredentialRequest = {
        display_name: req.display_name,
        base_url: req.base_url,
        model: req.model,
        haiku_model: req.haiku_model,
        sonnet_model: req.sonnet_model,
        opus_model: req.opus_model,
        subagent_model: req.subagent_model,
      };
      if (req.api_key) patch.api_key = req.api_key;
      await API.updateAgentCredential(editingCred.id, patch);
      setEditingCred(null);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [editingCred, loadCreds, t],
  );

  const credentialsRef = useRef<AgentCredential[]>([]);
  useEffect(() => {
    credentialsRef.current = credentials;
  }, [credentials]);

  const handleActivate = useCallback(
    async (id: number) => {
      setBusyCredId(id);
      try {
        await API.activateAgentCredential(id);
        await loadCreds();
        const c = credentialsRef.current.find((x) => x.id === id);
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
    [loadCreds, t],
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
        setTestResult(null);
        setTestedCredId(null);
      }
    },
    [testedCredId, loadCreds, t],
  );

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

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-7 pb-0 pt-1">
        {/* Page intro */}
        <div>
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
            </div>
          </div>
          {/* 提示 pill 与下方 Section 左边对齐,不再嵌套在文本块里 */}
          <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2">
            <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-text-4" aria-hidden />
            <p className="text-[11.5px] leading-[1.55] text-text-3">
              {t("claude_code_compat_hint")}
            </p>
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
            testedId={testedCredId}
            testResult={testResult}
            onApplyFix={(suggestedUrl) => void handleApplyFix(suggestedUrl)}
            onActivate={(id) => void handleActivate(id)}
            onTest={(id) => void handleTest(id)}
            onEdit={handleEdit}
            onDelete={requestDelete}
          />
        </Section>

        {/* Section 2: Runtime tuning */}
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
        disabled={false}
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

      <AddCredentialModal
        key={editingCred?.id ?? "edit-empty"}
        open={editingCred !== null}
        mode="edit"
        presets={presets}
        customSentinelId={customSentinelId}
        initial={
          editingCred
            ? {
                preset_id: editingCred.preset_id,
                display_name: editingCred.display_name,
                base_url: editingCred.base_url,
                model: editingCred.model ?? undefined,
                haiku_model: editingCred.haiku_model ?? undefined,
                sonnet_model: editingCred.sonnet_model ?? undefined,
                opus_model: editingCred.opus_model ?? undefined,
                subagent_model: editingCred.subagent_model ?? undefined,
              }
            : undefined
        }
        onSubmit={handleUpdate}
        onClose={() => setEditingCred(null)}
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
