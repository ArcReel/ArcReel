import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Loader2 } from "lucide-react";

import { API, type AgentProfileStatus } from "@/api";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { errMsg, voidCall } from "@/utils/async";

import { SettingsBlock, TabHeader } from "./SettingsBlock";

function sameFiles(left: string[], right: string[]) {
  return left.length === right.length && left.every((file, index) => file === right[index]);
}

function FileList({ files, label }: { files: string[]; label?: string }) {
  return (
    <ul aria-label={label} className="flex flex-col gap-1">
      {files.map((file) => (
        <li key={file} className="font-mono text-xs break-all text-muted-foreground">
          {file}
        </li>
      ))}
    </ul>
  );
}

/** 「Agent 配置」：项目内 Agent 配置的状态，以及「重置为内置配置」。重置不可撤销，经 AlertDialog 确认。 */
export function AgentProfileTab({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [status, setStatus] = useState<AgentProfileStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadCount, setLoadCount] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      (async () => {
        try {
          const next = await API.getAgentProfileStatus(projectName, { signal: controller.signal });
          setStatus(next);
          setLoadError(null);
        } catch (error) {
          if (controller.signal.aborted) return;
          setLoadError(errMsg(error));
        }
      })(),
    );
    return () => controller.abort();
  }, [projectName, loadCount]);

  // 打开确认前取一次最新状态，列出的就是将被丢弃的文件
  const openReset = async () => {
    try {
      const latest = await API.getAgentProfileStatus(projectName);
      setStatus(latest);
      setConfirmOpen(latest.customized);
    } catch (error) {
      useAppStore.getState().pushToast(t("agent_profile_reset_failed", { message: errMsg(error) }), "error");
    }
  };

  const reset = async () => {
    setResetting(true);
    try {
      // 确认期间定制文件有变化时不重置：刷新列表，由创作者对新列表再确认一次
      const latest = await API.getAgentProfileStatus(projectName);
      if (!status || !sameFiles(status.customized_files, latest.customized_files)) {
        setStatus(latest);
        if (!latest.customized) setConfirmOpen(false);
        return;
      }
      setStatus(await API.resetAgentProfile(projectName));
      setConfirmOpen(false);
    } catch (error) {
      useAppStore.getState().pushToast(t("agent_profile_reset_failed", { message: errMsg(error) }), "error");
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <TabHeader title={t("agent_profile_title")} description={t("agent_profile_description")} />

      {loadError ? (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertDescription>{t("agent_profile_load_failed", { message: loadError })}</AlertDescription>
          <AlertAction>
            <Button variant="outline" size="xs" onClick={() => {
                setLoadError(null);
                setLoadCount((count) => count + 1);
              }}>
              {t("common:retry")}
            </Button>
          </AlertAction>
        </Alert>
      ) : !status ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          {t("common:loading")}
        </p>
      ) : (
        <SettingsBlock title={status.customized ? t("agent_profile_customized") : t("agent_profile_builtin")}>
          {status.customized && (
            <>
              <FileList files={status.customized_files} label={t("agent_profile_affected_files")} />
              <div>
                <Button variant="outline" onClick={() => voidCall(openReset())}>
                  {t("agent_profile_reset")}
                </Button>
              </div>
            </>
          )}
        </SettingsBlock>
      )}

      <AlertDialog
        open={confirmOpen}
        onOpenChange={(next) => {
          // 重置请求在途时不响应关闭，Esc 也关不掉
          if (!next && !resetting) setConfirmOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("agent_profile_reset_confirm_title")}</AlertDialogTitle>
            <AlertDialogDescription>{t("agent_profile_reset_confirm_description")}</AlertDialogDescription>
          </AlertDialogHeader>
          {/* 文件多时正文滚动，可聚焦以便键盘滚动 */}
          <AlertDialogBody
            tabIndex={0}
            role="region"
            aria-label={t("agent_profile_affected_files")}
          >
            <FileList files={status?.customized_files ?? []} />
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={resetting}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={resetting} onClick={() => voidCall(reset())}>
              {resetting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {resetting ? t("agent_profile_resetting") : t("agent_profile_reset_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
