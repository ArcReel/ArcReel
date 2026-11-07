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

/** Agent configuration: project profile states and Reset to built-in. Irreversible resets require AlertDialog confirmation. */
export function AgentProfileTab({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [status, setStatus] = useState<AgentProfileStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadCount, setLoadCount] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [resetting, setResetting] = useState(false);

  // react-doctor-disable-next-line react-doctor/no-set-state-after-await-in-effect -- postawait writes first check controller.signal.aborted; cleanup abort invalidates previous rounds
  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      (async () => {
        try {
          const next = await API.getAgentProfileStatus(projectName, { signal: controller.signal });
          if (controller.signal.aborted) return;
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

  // Read latest states before confirmations, listing exactly the files to discard.
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
      // Customized files changing during confirmation prevent reset; refresh lists for creators to confirm again.
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
          // In-flight resets block closing, including Esc.
          if (!next && !resetting) setConfirmOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("agent_profile_reset_confirm_title")}</AlertDialogTitle>
            <AlertDialogDescription>{t("agent_profile_reset_confirm_description")}</AlertDialogDescription>
          </AlertDialogHeader>
          {/* With many files, bodies scroll and remain focusable for keyboard scrolling. */}
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
