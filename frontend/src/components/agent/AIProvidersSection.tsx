import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { GripVertical, Pencil, Trash2, Zap, Check, Loader2, Plus, ChevronDown, ChevronUp } from "lucide-react";

import { API } from "@/api";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { SectionShell } from "@/components/ui/SectionShell";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type {
  AgentCredential,
  CreateAgentCredentialRequest,
  PresetProvider,
  TestConnectionResponse,
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
import { errMsg, voidCall } from "@/utils/async";

export function AIProvidersSection() {
  const { t } = useTranslation("dashboard");

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

  // Drag and drop state
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const loadCreds = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([
        API.listAgentCredentials(),
        API.listAgentPresetProviders(),
      ]);
      // Sort by priority
      const sorted = c.credentials.sort((a, b) => a.priority - b.priority);
      setCredentials(sorted);
      setPresets(p.providers);
      setCustomSentinelId(p.custom_sentinel_id);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    void loadCreds();
  }, [loadCreds]);

  const credentialsRef = useRef<AgentCredential[]>([]);
  useEffect(() => {
    credentialsRef.current = credentials;
  }, [credentials]);

  const handleCreate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      await API.createAgentCredential(req);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [loadCreds, t],
  );

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

  const handleTest = useCallback(async (id: number) => {
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

  // Drag and drop handlers
  const handleDragStart = (index: number) => {
    setDragIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    setDragOverIndex(index);
  };

  const handleDrop = async (targetIndex: number) => {
    if (dragIndex === null || dragIndex === targetIndex) {
      setDragIndex(null);
      setDragOverIndex(null);
      return;
    }

    const newCredentials = [...credentials];
    const [moved] = newCredentials.splice(dragIndex, 1);
    newCredentials.splice(targetIndex, 0, moved);

    // Update priorities
    const priorityUpdates = newCredentials.map((cred, idx) => ({
      id: cred.id,
      priority: idx,
    }));

    setCredentials(newCredentials);
    setDragIndex(null);
    setDragOverIndex(null);

    try {
      await API.reorderAgentCredentials(priorityUpdates);
      useAppStore.getState().pushToast(t("priority_saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      await loadCreds(); // Revert on error
    }
  };

  const handleDragEnd = () => {
    setDragIndex(null);
    setDragOverIndex(null);
  };

  // Move up/down buttons (alternative to drag)
  const handleMoveUp = async (index: number) => {
    if (index === 0) return;
    const newCredentials = [...credentials];
    [newCredentials[index - 1], newCredentials[index]] = [newCredentials[index], newCredentials[index - 1]];
    const priorityUpdates = newCredentials.map((cred, idx) => ({ id: cred.id, priority: idx }));
    setCredentials(newCredentials);
    try {
      await API.reorderAgentCredentials(priorityUpdates);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      await loadCreds();
    }
  };

  const handleMoveDown = async (index: number) => {
    if (index === credentials.length - 1) return;
    const newCredentials = [...credentials];
    [newCredentials[index], newCredentials[index + 1]] = [newCredentials[index + 1], newCredentials[index]];
    const priorityUpdates = newCredentials.map((cred, idx) => ({ id: cred.id, priority: idx }));
    setCredentials(newCredentials);
    try {
      await API.reorderAgentCredentials(priorityUpdates);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      await loadCreds();
    }
  };

  return (
    <>
      <SectionShell
        kicker="AI"
        title={t("ai_providers")}
        description={t("ai_providers_desc")}
        trailing={
          <button
            type="button"
            onClick={() => setAddModalOpen(true)}
            className={GHOST_BTN_CLS}
          >
            <Plus className="size-3.5" />
            {t("add_provider")}
          </button>
        }
      >
        {credentials.length === 0 ? (
          <p className="text-sm text-zinc-400">{t("no_providers")}</p>
        ) : (
          <div className="space-y-2">
            {credentials.map((cred, index) => (
              <div
                key={cred.id}
                draggable
                onDragStart={() => handleDragStart(index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDrop={() => void handleDrop(index)}
                onDragEnd={handleDragEnd}
                className={`flex items-center gap-3 rounded-lg border p-3 transition-colors ${
                  dragOverIndex === index
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-zinc-700/50 bg-zinc-900/50"
                } ${cred.is_active ? "ring-1 ring-green-500/30" : ""}`}
              >
                {/* Drag handle */}
                <div className="cursor-grab text-zinc-500 hover:text-zinc-300">
                  <GripVertical className="size-4" />
                </div>

                {/* Priority number */}
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-800 text-xs font-medium text-zinc-400">
                  {index + 1}
                </div>

                {/* Provider info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-zinc-200 truncate">{cred.display_name}</span>
                    {cred.is_active && (
                      <span className="rounded-full bg-green-500/20 px-2 py-0.5 text-[10px] font-medium text-green-400">
                        {t("active")}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-zinc-500 truncate">{cred.base_url}</div>
                  {cred.model && (
                    <div className="text-xs text-zinc-400 mt-0.5">
                      Model: {cred.model}
                    </div>
                  )}
                </div>

                {/* Move buttons */}
                <div className="flex flex-col gap-0.5">
                  <button
                    type="button"
                    onClick={() => void handleMoveUp(index)}
                    disabled={index === 0}
                    className="p-0.5 text-zinc-500 hover:text-zinc-300 disabled:opacity-30"
                  >
                    <ChevronUp className="size-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleMoveDown(index)}
                    disabled={index === credentials.length - 1}
                    className="p-0.5 text-zinc-500 hover:text-zinc-300 disabled:opacity-30"
                  >
                    <ChevronDown className="size-3" />
                  </button>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1">
                  {!cred.is_active && (
                    <button
                      type="button"
                      onClick={() => void handleActivate(cred.id)}
                      disabled={busyCredId === cred.id}
                      className="rounded px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 disabled:opacity-50"
                    >
                      {busyCredId === cred.id ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        t("set_active")
                      )}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void handleTest(cred.id)}
                    disabled={busyCredId === cred.id}
                    className="rounded p-1 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-300 disabled:opacity-50"
                    title={t("test_connection")}
                  >
                    <Zap className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingCred(cred)}
                    className="rounded p-1 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-300"
                    title={t("edit")}
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteId(cred.id)}
                    disabled={cred.is_active}
                    className="rounded p-1 text-zinc-500 hover:bg-red-900/50 hover:text-red-400 disabled:opacity-30"
                    title={cred.is_active ? t("cannot_delete_active") : t("delete")}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Test result */}
        {testResult && testedCredId && (
          <div className={`mt-3 rounded-lg border p-3 text-sm ${
            testResult.overall === "ok"
              ? "border-green-500/30 bg-green-500/10 text-green-300"
              : testResult.overall === "warn"
                ? "border-yellow-500/30 bg-yellow-500/10 text-yellow-300"
                : "border-red-500/30 bg-red-500/10 text-red-300"
          }`}>
            <div className="font-medium">
              {testResult.overall === "ok" ? "✅ " : testResult.overall === "warn" ? "⚠️ " : "❌ "}
              {testResult.overall === "ok" ? t("test_ok") : testResult.overall === "warn" ? t("test_warn") : t("test_fail")}
            </div>
            {testResult.messages_probe?.error && (
              <div className="mt-1 text-xs opacity-80">{testResult.messages_probe.error}</div>
            )}
          </div>
        )}

        <p className="mt-4 text-xs text-zinc-500">
          {t("fallback_hint")}
        </p>
      </SectionShell>

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
    </>
  );
}
