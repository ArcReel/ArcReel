import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Check, ChevronRight, CircleAlert, Copy, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  ObjectStorageForceDisconnectResult,
  ObjectStorageRelocationMode,
  ObjectStorageRelocationPlan,
  ObjectStorageRelocationProgress,
  ObjectStorageRelocationRunResult,
  ObjectStorageRelocationTargetPayload,
  ObjectStorageSettings,
  ObjectStorageUnrecoverableItem,
} from "@/types";
import { errMsg } from "@/utils/async";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return index === 0 ? `${Math.round(value)} ${units[index]}` : `${value.toFixed(1)} ${units[index]}`;
}

export function RelocationDialog({
  mode,
  target,
  initialDeleteRemote = true,
  onClose,
  onCompleted,
  onCancelled,
  onRequestForceDisconnect,
}: {
  mode: ObjectStorageRelocationMode;
  target?: ObjectStorageRelocationTargetPayload | null;
  initialDeleteRemote?: boolean;
  onClose: () => void;
  onCompleted: (result: ObjectStorageRelocationRunResult) => void;
  onCancelled: (settings: ObjectStorageSettings) => void;
  onRequestForceDisconnect: (items: ObjectStorageUnrecoverableItem[]) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const keepRemoteId = useId();
  const [plan, setPlan] = useState<ObjectStorageRelocationPlan | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(true);
  const [planError, setPlanError] = useState<unknown>(null);
  const [keepRemote, setKeepRemote] = useState(!initialDeleteRemote);
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [progress, setProgress] = useState<ObjectStorageRelocationProgress | null>(null);
  const [incomplete, setIncomplete] = useState<ObjectStorageRelocationRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const planController = useRef<AbortController | null>(null);
  const loadPlan = useCallback(async () => {
    planController.current?.abort();
    const controller = new AbortController();
    planController.current = controller;
    setLoadingPlan(true);
    setPlanError(null);
    try {
      const next = await API.getObjectStorageRelocationPlan({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setPlan(next);
      if (next.relocating && next.relocate_delete_remote !== null) {
        setKeepRemote(!next.relocate_delete_remote);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setPlanError(error);
    } finally {
      if (!controller.signal.aborted) setLoadingPlan(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 弹层打开时拉取收敛影响评估，属于受控的初始化加载
    void loadPlan();
    return () => planController.current?.abort();
  }, [loadPlan]);

  useEffect(() => {
    if (!running) return;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = await API.getObjectStorageRelocationStatus({ signal: controller.signal });
        if (!controller.signal.aborted) setProgress(next);
      } catch {
        return;
      }
    };
    const timer = window.setInterval(() => void poll(), 300);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [running]);

  const busy = running || cancelling;
  const reusableCount = plan ? Math.max(0, plan.in_use_object_count - plan.download_object_count) : 0;
  const inFlight = plan?.in_flight_uploads ?? 0;

  const startOrRetry = async () => {
    if (busy || loadingPlan) return;
    setRunning(true);
    setRunError(null);
    setIncomplete(null);
    try {
      const result = await API.runObjectStorageRelocation({
        mode,
        delete_remote_objects: !keepRemote,
        target: mode === "switch" ? (target ?? null) : null,
      });
      if (result.status === "completed") {
        onCompleted(result);
        return;
      }
      setIncomplete(result);
      void loadPlan();
    } catch (error) {
      setRunError(errMsg(error));
    } finally {
      setRunning(false);
    }
  };

  const handleCancelConvergence = async () => {
    if (busy) return;
    setCancelling(true);
    setRunError(null);
    try {
      const settings = await API.cancelObjectStorageRelocation();
      onCancelled(settings);
    } catch (error) {
      setRunError(errMsg(error));
    } finally {
      setCancelling(false);
    }
  };

  const showCancelConvergence = Boolean(plan?.relocating || incomplete);
  const canForceDisconnect = Boolean(incomplete?.unreachable || plan?.relocating);

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>
            {t(mode === "switch" ? "object_storage_relocation_title_switch" : "object_storage_relocation_title_delete")}
          </DialogTitle>
          <DialogDescription>
            {t(mode === "switch" ? "object_storage_relocation_desc_switch" : "object_storage_relocation_desc_delete")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-3">
            {loadingPlan && !plan ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
                {t("object_storage_relocation_loading_plan")}
              </p>
            ) : planError !== null && !plan ? (
              <RequestErrorNotice
                error={planError}
                action={{ label: t("common:retry"), onClick: () => void loadPlan() }}
              />
            ) : (
              plan && (
                <>
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 rounded-md border border-border bg-muted/30 px-3 py-2.5 text-sm">
                    <dt className="text-muted-foreground">{t("object_storage_relocation_stat_in_use")}</dt>
                    <dd className="font-medium text-foreground">
                      {t("object_storage_relocation_stat_in_use_value", {
                        objects: plan.in_use_object_count,
                        references: plan.in_use_reference_count,
                      })}
                    </dd>
                    <dt className="text-muted-foreground">{t("object_storage_relocation_stat_download")}</dt>
                    <dd className="font-medium text-foreground">
                      {t("object_storage_relocation_stat_download_value", {
                        count: plan.download_object_count,
                        size: formatBytes(plan.estimated_download_bytes),
                      })}
                    </dd>
                    <dt className="text-muted-foreground">{t("object_storage_relocation_stat_reuse")}</dt>
                    <dd className="text-foreground">
                      {t("object_storage_relocation_stat_reuse_value", { count: reusableCount })}
                    </dd>
                  </dl>

                  {plan.pending_ingest_tasks > 0 && (
                    <div
                      role="status"
                      className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn"
                    >
                      <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                      <p className="min-w-0 flex-1 wrap-break-word">
                        {t("object_storage_pending_ingest", { count: plan.pending_ingest_tasks })}
                      </p>
                    </div>
                  )}

                  {inFlight > 0 && (
                    <div
                      role="alert"
                      className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn"
                    >
                      <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                      <p className="min-w-0 flex-1 wrap-break-word">
                        {t("object_storage_relocation_in_flight_warning", { count: inFlight })}
                      </p>
                    </div>
                  )}

                  <div className="flex flex-col gap-1.5 rounded-md border border-border px-3 py-2.5">
                    <div className="flex items-start gap-2">
                      <Checkbox
                        id={keepRemoteId}
                        checked={keepRemote}
                        onCheckedChange={setKeepRemote}
                        disabled={busy}
                      />
                      <label htmlFor={keepRemoteId} className="text-sm font-medium">
                        {t("object_storage_relocation_keep_remote_label")}
                      </label>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {t(
                        keepRemote
                          ? "object_storage_relocation_keep_remote_desc_checked"
                          : "object_storage_relocation_keep_remote_desc_unchecked",
                      )}
                    </p>
                  </div>
                </>
              )
            )}

            {running && (
              <div
                aria-live="polite"
                className="flex flex-col gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-3 py-2.5 text-xs"
              >
                <p className="flex items-center gap-1.5 font-medium text-foreground">
                  <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />
                  {t("object_storage_relocation_running")}
                </p>
                {progress && progress.total_objects > 0 && (
                  <p className="text-muted-foreground">
                    {t("object_storage_relocation_progress", {
                      completed: progress.completed_objects,
                      total: progress.total_objects,
                      downloaded: progress.downloaded_objects,
                      reused: progress.reused_local_objects,
                    })}
                  </p>
                )}
              </div>
            )}

            {incomplete && (
              <div
                role="alert"
                className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-xs"
              >
                <p className="font-medium text-destructive">{t("object_storage_relocation_incomplete_title")}</p>
                {incomplete.error_message && (
                  <p className="wrap-break-word text-foreground">{incomplete.error_message}</p>
                )}
                <p className="text-muted-foreground">{t("object_storage_relocation_incomplete_desc")}</p>
                {incomplete.total_objects > 0 && (
                  <p className="text-muted-foreground">
                    {t("object_storage_relocation_progress", {
                      completed: incomplete.completed_objects,
                      total: incomplete.total_objects,
                      downloaded: incomplete.downloaded_objects,
                      reused: incomplete.reused_local_objects,
                    })}
                  </p>
                )}
                {incomplete.conflicts_preserved > 0 && (
                  <p className="text-warn">
                    {t("object_storage_relocation_conflicts_note", { count: incomplete.conflicts_preserved })}
                  </p>
                )}
                {incomplete.error_detail && (
                  <Collapsible>
                    <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
                      <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
                      {t("common:error_details")}
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <p className="font-mono text-xs wrap-break-word text-muted-foreground" translate="no">
                        {incomplete.error_detail}
                      </p>
                    </CollapsibleContent>
                  </Collapsible>
                )}
              </div>
            )}

            {runError && (
              <p role="alert" className="text-sm wrap-break-word text-destructive">
                {runError}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {canForceDisconnect && (
            <Button
              variant="destructive"
              size="sm"
              className="mr-auto"
              disabled={busy}
              onClick={() =>
                onRequestForceDisconnect(incomplete?.unrecoverable_items ?? plan?.unrecoverable_items ?? [])
              }
            >
              {t("object_storage_relocating_force_disconnect_entry")}
            </Button>
          )}
          {showCancelConvergence ? (
            <Button variant="outline" disabled={busy} onClick={() => void handleCancelConvergence()}>
              {cancelling && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("object_storage_relocating_cancel")}
            </Button>
          ) : (
            <Button variant="outline" disabled={busy} onClick={onClose}>
              {t("common:cancel")}
            </Button>
          )}
          <Button
            variant={mode === "delete" ? "destructive" : "default"}
            disabled={busy || loadingPlan || !plan || inFlight > 0}
            onClick={() => void startOrRetry()}
          >
            {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {incomplete
              ? t("object_storage_relocation_retry")
              : t(mode === "switch" ? "object_storage_relocation_start_switch" : "object_storage_relocation_start_delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ForceDisconnectDialog({
  initialItems,
  onClose,
  onDisconnected,
}: {
  initialItems?: ObjectStorageUnrecoverableItem[] | null;
  onClose: () => void;
  onDisconnected: (result: ObjectStorageForceDisconnectResult) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [items, setItems] = useState<ObjectStorageUnrecoverableItem[]>(initialItems ?? []);
  const [loading, setLoading] = useState(initialItems == null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const loadController = useRef<AbortController | null>(null);
  const fetchItems = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    setLoading(true);
    setLoadError(null);
    try {
      const plan = await API.getObjectStorageRelocationPlan({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setItems(plan.unrecoverable_items);
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoadError(err);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialItems != null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 未传清单时于弹层打开拉取无法拉回列表，属于受控的初始化加载
    void fetchItems();
    return () => loadController.current?.abort();
  }, [initialItems, fetchItems]);

  const handleCopy = async () => {
    const text = items.map((item) => `${item.holder_label}\t${item.path}\t${item.digest}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      return;
    }
  };

  const handleConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await API.forceDisconnectObjectStorage();
      onDisconnected(result);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !submitting) onClose();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("object_storage_force_disconnect_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("object_storage_force_disconnect_desc")}</AlertDialogDescription>

            {loading ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />
                {t("object_storage_relocation_loading_plan")}
              </p>
            ) : loadError !== null ? (
              <RequestErrorNotice
                error={loadError}
                action={{ label: t("common:retry"), onClick: () => void fetchItems() }}
              />
            ) : items.length === 0 ? (
              <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-foreground">
                {t("object_storage_force_disconnect_all_salvageable")}
              </p>
            ) : (
              <div className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-destructive">
                    {t("object_storage_force_disconnect_missing_title", { count: items.length })}
                  </p>
                  <Button variant="outline" size="xs" onClick={() => void handleCopy()}>
                    {copied ? <Check aria-hidden data-icon="inline-start" /> : <Copy aria-hidden data-icon="inline-start" />}
                    {t(copied ? "object_storage_force_disconnect_copied" : "object_storage_force_disconnect_copy")}
                  </Button>
                </div>
                <ul className="relative flex max-h-48 flex-col gap-1.5 overflow-y-auto text-xs">
                  {items.map((item) => (
                    <li
                      key={`${item.holder_kind}:${item.holder_label}:${item.path}:${item.digest}`}
                      className="flex flex-col gap-0.5 rounded border border-border/60 bg-background/80 px-2 py-1.5"
                    >
                      <span className="font-medium wrap-break-word text-foreground">{item.holder_label}</span>
                      <span className="font-mono wrap-break-word text-muted-foreground" translate="no">
                        {item.path} · {item.digest.slice(0, 12)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && (
              <p role="alert" className="text-sm wrap-break-word text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting || loading} onClick={() => void handleConfirm()}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("object_storage_force_disconnect_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
