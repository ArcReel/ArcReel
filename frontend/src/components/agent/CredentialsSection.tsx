import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { CredentialList } from "@/components/agent/CredentialList";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
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

/**
 * Agent 供应商列表（数据上是 Agent 凭证）。增改、删除、切换生效都是即时动作：成功以列表变化为反馈，
 * 失败弹出提示；删除不可撤销，先经 AlertDialog 确认。
 */
export function CredentialsSection() {
  const { t } = useTranslation("dashboard");

  const [credentials, setCredentials] = useState<AgentCredential[]>([]);
  const [presets, setPresets] = useState<PresetProvider[]>([]);
  const [customSentinelId, setCustomSentinelId] = useState("__custom__");
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [busyCredId, setBusyCredId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
  const [testedCredId, setTestedCredId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentCredential | null>(null);
  const [deletingCred, setDeletingCred] = useState(false);
  const [editingCred, setEditingCred] = useState<AgentCredential | null>(null);

  const loadController = useRef<AbortController | null>(null);
  const loadCreds = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const [c, p] = await Promise.all([API.listAgentCredentials({ signal: controller.signal }), API.listAgentPresetProviders({ signal: controller.signal })]);
      if (controller.signal.aborted) return;
      setCredentials(c.credentials);
      setPresets(p.providers);
      setCustomSentinelId(p.custom_sentinel_id);
    } catch (err) {
      if (controller.signal.aborted) return;
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 时异步拉取 Agent 供应商后回写，属于受控的初始化加载
    void loadCreds();
    return () => loadController.current?.abort();
  }, [loadCreds]);

  const afterChange = useCallback(async () => {
    await loadCreds();
    voidCall(useConfigStatusStore.getState().refresh());
  }, [loadCreds]);

  const handleCreate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      await API.createAgentCredential(req);
      await afterChange();
    },
    [afterChange],
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
      await afterChange();
    },
    [editingCred, afterChange],
  );

  const handleActivate = useCallback(
    async (id: number) => {
      setBusyCredId(id);
      try {
        await API.activateAgentCredential(id);
        await afterChange();
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        setBusyCredId(null);
      }
    },
    [afterChange],
  );

  const handleTest = useCallback(async (id: number) => {
    setBusyCredId(id);
    setTestResult(null);
    setTestedCredId(id);
    try {
      setTestResult(await API.testAgentCredential(id));
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setBusyCredId(null);
    }
  }, []);

  const confirmDelete = useCallback(async () => {
    if (deleteTarget == null) return;
    setDeletingCred(true);
    try {
      await API.deleteAgentCredential(deleteTarget.id);
      await afterChange();
      setDeleteTarget(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeletingCred(false);
    }
  }, [deleteTarget, afterChange]);

  return (
    <section aria-labelledby="agent-providers-title" className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id="agent-providers-title" className="text-base font-medium">
            {t("agent_credentials")}
          </h3>
          <p className="text-sm text-muted-foreground">{t("agent_providers_desc")}</p>
        </div>
        <Button variant="outline" onClick={() => setAddModalOpen(true)} className="shrink-0">
          <Plus aria-hidden data-icon="inline-start" />
          {t("add_credential")}
        </Button>
      </div>

      <CredentialList
        credentials={credentials}
        busyId={busyCredId}
        testedId={testedCredId}
        testResult={testResult}
        onActivate={(id) => void handleActivate(id)}
        onTest={(id) => void handleTest(id)}
        onEdit={setEditingCred}
        onDelete={setDeleteTarget}
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

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          // 删除请求在途时不响应 Esc，避免对话框先于结果消失
          if (!next && !deletingCred) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("cred_delete_confirm_title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("cred_delete_confirm", { name: deleteTarget?.display_name ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingCred}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={deletingCred} onClick={() => void confirmDelete()}>
              {deletingCred ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {t("cred_delete_action")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
