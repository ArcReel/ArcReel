import { useCallback, useEffect, useId, useRef, useState, type Ref } from "react";
import { ChevronRight, CircleAlert, CircleCheck, CircleX, Loader2, Minus, Pencil, Plus, Trash2, Wifi } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { RequestErrorNotice } from "@/components/shared/RequestErrorNotice";
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type {
  NewImageTarget,
  ObjectStorageAddressingStyle,
  ObjectStorageCleanupResult,
  ObjectStorageConnection,
  ObjectStorageForceDisconnectResult,
  ObjectStorageProbeDiagnostic,
  ObjectStorageProbeStep,
  ObjectStorageProbeStepName,
  ObjectStorageProbeStepStatus,
  ObjectStorageRelocationMode,
  ObjectStorageRelocationRunResult,
  ObjectStorageRelocationTargetPayload,
  ObjectStorageSettings,
  ObjectStorageTestResult,
  ObjectStorageUnrecoverableItem,
  SavedObjectStorageConnection,
} from "@/types";
import { errMsg } from "@/utils/async";
import { cn } from "cn";

import { ForceDisconnectDialog, RelocationDialog } from "./ObjectStorageRelocationDialogs";
import {
  OBJECT_STORAGE_PRESETS,
  buildPresetEndpoint,
  detectPresetForEndpoint,
  getObjectStoragePreset,
  hasEndpointPlaceholder,
  type ObjectStoragePresetId,
} from "./object-storage-presets";

const LABEL_CLS = "text-sm font-medium text-foreground";

const ADDRESSING_STYLE_LABEL_KEYS: Record<ObjectStorageAddressingStyle, string> = {
  auto: "object_storage_addressing_style_auto",
  virtual: "object_storage_addressing_style_virtual",
  path: "object_storage_addressing_style_path",
};

const PROBE_STEP_LABEL_KEYS: Record<ObjectStorageProbeStepName, string> = {
  head_bucket: "object_storage_probe_step_head_bucket",
  put_object: "object_storage_probe_step_put_object",
  head_object: "object_storage_probe_step_head_object",
  presigned_get: "object_storage_probe_step_presigned_get",
  delete_object: "object_storage_probe_step_delete_object",
};

const PROBE_STATUS_LABEL_KEYS: Record<ObjectStorageProbeStepStatus, string> = {
  passed: "object_storage_probe_status_passed",
  failed: "object_storage_probe_status_failed",
  skipped: "object_storage_probe_status_skipped",
};

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; settings: ObjectStorageSettings };

type TestState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "result"; result: ObjectStorageTestResult }
  | { status: "error"; error: unknown };

function formatProbeDiagnostic(diagnostic: ObjectStorageProbeDiagnostic | null | undefined): string | null {
  if (!diagnostic) return null;
  const parts: string[] = [];
  if (diagnostic.code) parts.push(diagnostic.code);
  if (typeof diagnostic.status === "number") parts.push(`HTTP ${diagnostic.status}`);
  if (diagnostic.message) parts.push(diagnostic.message);
  return parts.length > 0 ? parts.join(": ") : null;
}

type RelocationModalState = {
  mode: ObjectStorageRelocationMode;
  target?: ObjectStorageRelocationTargetPayload | null;
  initialDeleteRemote?: boolean;
};

type ForceDisconnectModalState = {
  items?: ObjectStorageUnrecoverableItem[] | null;
};

export function ObjectStorageBlock() {
  const { t } = useTranslation(["dashboard", "common"]);
  const headingId = useId();
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [cleanupFailures, setCleanupFailures] = useState<Record<string, number>>({});
  const [relocationResult, setRelocationResult] = useState<ObjectStorageRelocationRunResult | null>(null);
  const [forceDisconnectResult, setForceDisconnectResult] = useState<ObjectStorageForceDisconnectResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [relocationModal, setRelocationModal] = useState<RelocationModalState | null>(null);
  const [forceDisconnectModal, setForceDisconnectModal] = useState<ForceDisconnectModalState | null>(null);
  const [cancellingRelocation, setCancellingRelocation] = useState(false);
  const [relocationCancelError, setRelocationCancelError] = useState<string | null>(null);
  const [test, setTest] = useState<TestState>({ status: "idle" });

  const loadController = useRef<AbortController | null>(null);
  const fetchConnection = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const settings = await API.getObjectStorageConnection({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setLoad({ status: "ready", settings });
    } catch (error) {
      if (controller.signal.aborted) return;
      setLoad({ status: "error", error });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时拉取连接配置后回写，属于受控的初始化加载
    void fetchConnection();
    return () => loadController.current?.abort();
  }, [fetchConnection]);

  const testGeneration = useRef(0);
  const resetTest = useCallback(() => {
    testGeneration.current += 1;
    setTest({ status: "idle" });
  }, []);
  const closeEditor = useCallback(() => setEditing(false), []);
  const patchSettings = useCallback((patch: Partial<ObjectStorageSettings>) => {
    setLoad((prev) => (prev.status === "ready" ? { status: "ready", settings: { ...prev.settings, ...patch } } : prev));
  }, []);
  const refreshPendingIngest = useCallback(async () => {
    try {
      const latest = await API.getObjectStorageConnection();
      patchSettings({ pending_ingest_tasks: latest.pending_ingest_tasks });
    } catch {
      return;
    }
  }, [patchSettings]);
  const handleSaved = useCallback(
    (saved: SavedObjectStorageConnection) => {
      setCleanupFailures(saved.cleanup_failures);
      setRelocationResult(null);
      setForceDisconnectResult(null);
      setEditing(false);
      resetTest();
      patchSettings({ connection: saved });
      void fetchConnection();
    },
    [resetTest, patchSettings, fetchConnection],
  );
  const handleRelocationCompleted = useCallback(
    (result: ObjectStorageRelocationRunResult) => {
      setRelocationModal(null);
      setEditing(false);
      setDeleting(false);
      resetTest();
      setForceDisconnectResult(null);
      setRelocationResult(result);
      setCleanupFailures(result.cleanup_failures);
      patchSettings({
        connection: result.connection,
        new_image_target: result.new_image_target,
        pending_ingest_tasks: 0,
      });
      void fetchConnection();
    },
    [resetTest, patchSettings, fetchConnection],
  );
  const handleRelocationCancelled = useCallback(
    (nextSettings: ObjectStorageSettings) => {
      setRelocationModal(null);
      setRelocationCancelError(null);
      setLoad({ status: "ready", settings: nextSettings });
    },
    [],
  );
  const handleForceDisconnected = useCallback(
    (result: ObjectStorageForceDisconnectResult) => {
      setForceDisconnectModal(null);
      setRelocationModal(null);
      setEditing(false);
      setDeleting(false);
      resetTest();
      setRelocationResult(null);
      setCleanupFailures({});
      setForceDisconnectResult(result);
      patchSettings({
        connection: null,
        new_image_target: result.new_image_target,
        pending_ingest_tasks: 0,
      });
      void fetchConnection();
    },
    [resetTest, patchSettings, fetchConnection],
  );
  const handleBannerCancelRelocation = async () => {
    if (cancellingRelocation) return;
    setCancellingRelocation(true);
    setRelocationCancelError(null);
    try {
      const nextSettings = await API.cancelObjectStorageRelocation();
      setLoad({ status: "ready", settings: nextSettings });
    } catch (error) {
      setRelocationCancelError(errMsg(error));
    } finally {
      setCancellingRelocation(false);
    }
  };
  const handleTargetSaved = useCallback(
    (target: NewImageTarget) => patchSettings({ new_image_target: target }),
    [patchSettings],
  );
  const runTest = async () => {
    const generation = ++testGeneration.current;
    setTest({ status: "testing" });
    try {
      const result = await API.checkObjectStorageConnection();
      if (generation !== testGeneration.current) return;
      setTest({ status: "result", result });
      if (result.success) {
        setLoad((prev) =>
          prev.status === "ready" && prev.settings.connection?.needs_endpoint_review
            ? {
                status: "ready",
                settings: {
                  ...prev.settings,
                  connection: { ...prev.settings.connection, needs_endpoint_review: false },
                },
              }
            : prev,
        );
      }
    } catch (error) {
      if (generation === testGeneration.current) setTest({ status: "error", error });
    }
  };

  const settings = load.status === "ready" ? load.settings : null;
  const connection = settings?.connection ?? null;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex items-start gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 id={headingId} className="text-base font-medium">
            {t("object_storage_title")}
          </h3>
          <p className="text-sm text-muted-foreground">{t("object_storage_desc")}</p>
        </div>
        {load.status === "ready" && !connection && (
          <Button variant="outline" size="sm" className="shrink-0" onClick={() => setEditing(true)}>
            <Plus data-icon="inline-start" />
            {t("object_storage_configure")}
          </Button>
        )}
      </div>

      {load.status === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      ) : load.status === "error" ? (
        <RequestErrorNotice
          error={load.error}
          action={{ label: t("common:retry"), onClick: () => void fetchConnection() }}
        />
      ) : !connection ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {t("object_storage_empty")}
        </p>
      ) : (
        <ConnectionCard
          connection={connection}
          test={test}
          cancellingRelocation={cancellingRelocation}
          relocationCancelError={relocationCancelError}
          onTest={() => void runTest()}
          onEdit={() => {
            setEditing(true);
            void refreshPendingIngest();
          }}
          onDelete={() => {
            if ((connection.in_use_object_count ?? 0) > 0 || connection.relocating) {
              setRelocationModal({
                mode: "delete",
                initialDeleteRemote: connection.relocate_delete_remote ?? true,
              });
            } else {
              setDeleting(true);
              void refreshPendingIngest();
            }
          }}
          onResumeRelocation={() => {
            if (connection.relocate_mode === "delete") {
              setRelocationModal({
                mode: "delete",
                initialDeleteRemote: connection.relocate_delete_remote ?? true,
              });
            } else {
              setEditing(true);
            }
          }}
          onCancelRelocation={() => void handleBannerCancelRelocation()}
          onForceDisconnect={() => setForceDisconnectModal({ items: null })}
        />
      )}

      {settings && (
        <NewImageTargetField
          target={settings.new_image_target}
          connectionConfigured={connection !== null}
          relocating={Boolean(connection?.relocating)}
          onSaved={handleTargetSaved}
        />
      )}

      {editing && (
        <ObjectStorageDialog
          connection={connection}
          pendingIngestTasks={settings?.pending_ingest_tasks ?? 0}
          onCancel={closeEditor}
          onSaved={handleSaved}
          onStartRelocation={(target, deleteRemote) => {
            setEditing(false);
            setRelocationModal({ mode: "switch", target, initialDeleteRemote: deleteRemote });
          }}
        />
      )}
      {deleting && (
        <DeleteObjectStorageDialog
          pendingIngestTasks={settings?.pending_ingest_tasks ?? 0}
          newImageTarget={settings?.new_image_target ?? "local"}
          onClose={() => setDeleting(false)}
          onDeleted={(result, resetTarget) => {
            setDeleting(false);
            resetTest();
            setRelocationResult(null);
            setForceDisconnectResult(null);
            setCleanupFailures(result.cleanup_failures);
            patchSettings({
              connection: null,
              pending_ingest_tasks: 0,
              ...(resetTarget ? { new_image_target: "local" as const } : {}),
            });
          }}
        />
      )}
      {relocationModal && (
        <RelocationDialog
          mode={relocationModal.mode}
          target={relocationModal.target}
          initialDeleteRemote={relocationModal.initialDeleteRemote}
          onClose={() => setRelocationModal(null)}
          onCompleted={handleRelocationCompleted}
          onCancelled={handleRelocationCancelled}
          onRequestForceDisconnect={(items) => {
            setRelocationModal(null);
            setForceDisconnectModal({ items });
          }}
        />
      )}
      {forceDisconnectModal && (
        <ForceDisconnectDialog
          initialItems={forceDisconnectModal.items}
          onClose={() => setForceDisconnectModal(null)}
          onDisconnected={handleForceDisconnected}
        />
      )}
      {relocationResult && (
        <div role="status" className="flex flex-col gap-1 rounded-md border border-good/40 bg-good/10 px-3 py-2 text-xs text-foreground">
          <p>
            {t("object_storage_relocation_completed_notice", {
              objects: relocationResult.completed_objects,
              references: relocationResult.promoted_references,
            })}
          </p>
          {relocationResult.conflicts_preserved > 0 && (
            <p className="text-warn">
              {t("object_storage_relocation_conflicts_note", { count: relocationResult.conflicts_preserved })}
            </p>
          )}
        </div>
      )}
      {forceDisconnectResult && (
        <div role="status" className="flex flex-col gap-1 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-foreground">
          <p>
            {t("object_storage_force_disconnect_result", {
              objects: forceDisconnectResult.salvaged_objects,
              references: forceDisconnectResult.salvaged_references,
              missing: forceDisconnectResult.unrecoverable_items.length,
            })}
          </p>
          {forceDisconnectResult.conflicts_preserved > 0 && (
            <p className="text-warn">
              {t("object_storage_relocation_conflicts_note", { count: forceDisconnectResult.conflicts_preserved })}
            </p>
          )}
        </div>
      )}
      <CleanupFailures failures={cleanupFailures} />
    </section>
  );
}

function ConnectionCard({
  connection,
  test,
  cancellingRelocation,
  relocationCancelError,
  onTest,
  onEdit,
  onDelete,
  onResumeRelocation,
  onCancelRelocation,
  onForceDisconnect,
}: {
  connection: ObjectStorageConnection;
  test: TestState;
  cancellingRelocation: boolean;
  relocationCancelError: string | null;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onResumeRelocation: () => void;
  onCancelRelocation: () => void;
  onForceDisconnect: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <dl className="grid min-w-0 flex-1 grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">{t("object_storage_bucket")}</dt>
          <dd className="min-w-0">
            <TruncatedText text={connection.bucket} className="font-mono" />
          </dd>
          <dt className="text-muted-foreground">{t("object_storage_endpoint")}</dt>
          <dd className="min-w-0">
            <TruncatedText text={connection.endpoint} className="font-mono" />
          </dd>
          <dt className="text-muted-foreground">{t("object_storage_region")}</dt>
          <dd className="font-mono">{connection.region}</dd>
          <dt className="text-muted-foreground">{t("object_storage_addressing_style")}</dt>
          <dd>{t(ADDRESSING_STYLE_LABEL_KEYS[connection.addressing_style])}</dd>
          <dt className="text-muted-foreground">{t("object_storage_access_key_id")}</dt>
          <dd className="font-mono">{connection.access_key_id_masked}</dd>
        </dl>
        <div className="flex shrink-0 items-center gap-1 self-start">
          <Button variant="ghost" size="sm" disabled={test.status === "testing"} onClick={onTest}>
            {test.status === "testing" ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Wifi aria-hidden data-icon="inline-start" />
            )}
            {t("object_storage_test")}
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={t("object_storage_edit_title")} onClick={onEdit}>
            <Pencil aria-hidden />
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={t("object_storage_delete")} onClick={onDelete}>
            <Trash2 aria-hidden />
          </Button>
        </div>
      </div>
      {connection.needs_endpoint_review && (
        <div role="status" className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-2 text-xs text-warn">
          <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <p className="min-w-0 flex-1 wrap-break-word">{t("object_storage_needs_endpoint_review")}</p>
        </div>
      )}
      {connection.relocating && (
        <div role="status" className="flex flex-col gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2.5 text-xs">
          <div className="flex items-start gap-2 text-warn">
            <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="font-medium">{t("object_storage_relocating_banner_title")}</p>
              <p className="wrap-break-word text-foreground">{t("object_storage_relocating_banner_desc")}</p>
            </div>
          </div>
          {relocationCancelError && (
            <p role="alert" className="wrap-break-word text-destructive">
              {relocationCancelError}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="xs" disabled={cancellingRelocation} onClick={onResumeRelocation}>
              {t(
                connection.relocate_mode === "delete"
                  ? "object_storage_relocating_resume_delete"
                  : "object_storage_relocating_resume_switch",
              )}
            </Button>
            <Button variant="ghost" size="xs" disabled={cancellingRelocation} onClick={onCancelRelocation}>
              {cancellingRelocation && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("object_storage_relocating_cancel")}
            </Button>
            <Button variant="destructive" size="xs" disabled={cancellingRelocation} onClick={onForceDisconnect}>
              {t("object_storage_relocating_force_disconnect_entry")}
            </Button>
          </div>
        </div>
      )}
      <div aria-live="polite">
        {test.status === "testing" && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />
            {t("object_storage_test_running")}
          </p>
        )}
        {test.status === "result" && <ProbeResultPanel result={test.result} />}
        {test.status === "error" && <RequestErrorNotice error={test.error} className="text-xs" />}
      </div>
    </div>
  );
}

function ProbeResultPanel({ result }: { result: ObjectStorageTestResult }) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 px-3 py-2.5 text-xs">
      <div className="flex flex-col gap-1">
        {result.success ? (
          <>
            <p className="flex items-center gap-1.5 font-medium text-good">
              <CircleCheck aria-hidden className="size-3.5 shrink-0" />
              {t("object_storage_test_ok")}
            </p>
            <p className="text-muted-foreground">{t("object_storage_test_scope_note")}</p>
          </>
        ) : (
          <p className="flex items-center gap-1.5 font-medium text-destructive">
            <CircleX aria-hidden className="size-3.5 shrink-0" />
            {t("object_storage_test_failed_summary")}
          </p>
        )}
      </div>

      <ol className="flex flex-col gap-1.5 border-t border-border/60 pt-2">
        {result.steps.map((step) => (
          <ProbeStepRow key={step.step} step={step} />
        ))}
      </ol>

      {result.residual_object_key && (
        <div role="alert" className="flex flex-col gap-1 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-2 text-warn">
          <p className="font-medium">{t("object_storage_probe_residual_notice")}</p>
          <p className="font-mono wrap-break-word text-foreground" translate="no">
            {result.residual_object_key}
          </p>
        </div>
      )}
    </div>
  );
}

function ProbeStepRow({ step }: { step: ObjectStorageProbeStep }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const diagnostic = formatProbeDiagnostic(step.diagnostic);
  return (
    <li className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        {step.status === "passed" ? (
          <CircleCheck aria-hidden className="size-3.5 shrink-0 text-good" />
        ) : step.status === "failed" ? (
          <CircleX aria-hidden className="size-3.5 shrink-0 text-destructive" />
        ) : (
          <Minus aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 font-medium text-foreground">{t(PROBE_STEP_LABEL_KEYS[step.step])}</span>
        <span
          className={cn(
            "shrink-0",
            step.status === "passed" && "text-good",
            step.status === "failed" && "text-destructive",
            step.status === "skipped" && "text-muted-foreground",
          )}
        >
          {t(PROBE_STATUS_LABEL_KEYS[step.status])}
        </span>
      </div>
      {step.status === "failed" && step.message && (
        <p className="pl-5 wrap-break-word text-destructive">{step.message}</p>
      )}
      {step.status === "failed" && diagnostic && (
        <div className="pl-5">
          <Collapsible>
            <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
              <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
              {t("common:error_details")}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <p className="font-mono text-xs wrap-break-word text-muted-foreground" translate="no">
                {diagnostic}
              </p>
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}
    </li>
  );
}

function NewImageTargetField({
  target,
  connectionConfigured,
  relocating,
  onSaved,
}: {
  target: NewImageTarget;
  connectionConfigured: boolean;
  relocating: boolean;
  onSaved: (target: NewImageTarget) => void;
}) {
  const { t } = useTranslation("dashboard");
  const headingId = useId();
  const [saving, setSaving] = useState<NewImageTarget | null>(null);
  const [error, setError] = useState<unknown>(null);

  const choose = async (next: NewImageTarget) => {
    if (next === target || saving) return;
    setSaving(next);
    setError(null);
    try {
      const saved = await API.saveNewImageTarget(next);
      onSaved(saved.target);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(null);
    }
  };

  const options: { value: NewImageTarget; title: string; hint: string; disabled: boolean }[] = [
    {
      value: "local",
      title: t("new_image_target_local"),
      hint: t("new_image_target_local_hint"),
      disabled: false,
    },
    {
      value: "object_storage",
      title: t("new_image_target_object_storage"),
      hint: connectionConfigured
        ? t("new_image_target_object_storage_hint")
        : t("new_image_target_needs_connection"),
      disabled: (!connectionConfigured || relocating) && target !== "object_storage",
    },
  ];

  return (
    <div className="@container flex flex-col gap-2 pt-2">
      <div className="flex flex-col gap-1">
        <h4 id={headingId} className="text-sm font-medium">
          {t("new_image_target_title")}
        </h4>
        <p className="text-sm text-muted-foreground">{t("new_image_target_desc")}</p>
      </div>
      <RadioGroup
        aria-labelledby={headingId}
        value={saving ?? target}
        disabled={saving !== null}
        onValueChange={(next) => void choose(next as NewImageTarget)}
        className="@md:grid-cols-2"
      >
        {options.map((option) => (
          // eslint-disable-next-line jsx-a11y/label-has-associated-control -- 控件是嵌套的 RadioGroupItem（Base UI 单选），规则识别不到
          <label
            key={option.value}
            className="flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors duration-fast hover:bg-muted/50 has-data-checked:border-primary has-data-disabled:cursor-not-allowed has-data-disabled:opacity-60 has-data-disabled:hover:bg-transparent"
          >
            <RadioGroupItem value={option.value} disabled={option.disabled} className="mt-0.5" />
            <span className="flex min-w-0 flex-col gap-1">
              <span className="flex items-center gap-1.5 text-sm font-medium">
                {option.title}
                {saving === option.value && <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />}
              </span>
              <span className="text-xs text-muted-foreground">{option.hint}</span>
            </span>
          </label>
        ))}
      </RadioGroup>
      {error !== null && <RequestErrorNotice error={error} className="text-xs" />}
    </div>
  );
}

function CleanupOptions({
  checked,
  onChange,
  pendingIngestTasks,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  pendingIngestTasks: number;
  disabled: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const id = useId();
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <Checkbox id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} />
        <label htmlFor={id} className="text-sm">
          {t("object_storage_delete_remote_objects")}
        </label>
      </div>
      <p className="text-sm text-muted-foreground">{t(checked ? "object_storage_cleanup_desc_checked" : "object_storage_cleanup_desc_unchecked")}</p>
      {pendingIngestTasks > 0 && (
        <p className="text-sm text-muted-foreground">
          {t("object_storage_pending_ingest", { count: pendingIngestTasks })}
        </p>
      )}
    </div>
  );
}

const CLEANUP_REASON_KEYS = {
  auth: "object_storage_cleanup_reason_auth",
  denied: "object_storage_cleanup_reason_denied",
  network: "object_storage_cleanup_reason_network",
  invalid_config: "object_storage_cleanup_reason_invalid_config",
  unavailable: "object_storage_cleanup_reason_unavailable",
  provider: "object_storage_cleanup_reason_provider",
} as const;

function CleanupFailures({ failures }: { failures: Record<string, number> }) {
  const { t } = useTranslation("dashboard");
  if (!Object.keys(failures).length) return null;
  return (
    <div role="status" className="flex flex-col gap-1 text-sm text-muted-foreground">
      <p>
        {t("object_storage_cleanup_failed", { count: Object.values(failures).reduce((sum, count) => sum + count, 0) })}
      </p>
      {Object.entries(failures).map(([reason, count]) => (
        <p key={reason}>
          {t("object_storage_cleanup_failure_reason", {
            count,
            reason: t(CLEANUP_REASON_KEYS[reason as keyof typeof CLEANUP_REASON_KEYS] ?? CLEANUP_REASON_KEYS.provider),
          })}
        </p>
      ))}
    </div>
  );
}

function ObjectStorageDialog({
  connection,
  pendingIngestTasks,
  onCancel,
  onSaved,
  onStartRelocation,
}: {
  connection: ObjectStorageConnection | null;
  pendingIngestTasks: number;
  onCancel: () => void;
  onSaved: (connection: SavedObjectStorageConnection) => void;
  onStartRelocation: (target: ObjectStorageRelocationTargetPayload, deleteRemote: boolean) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idPrefix = useId();
  const regionRef = useRef<HTMLInputElement>(null);
  const [presetId, setPresetId] = useState<ObjectStoragePresetId>(() => detectPresetForEndpoint(connection?.endpoint));
  const [autoSyncEndpoint, setAutoSyncEndpoint] = useState(false);
  const [region, setRegion] = useState(connection?.region ?? "");
  const [endpoint, setEndpoint] = useState(connection?.endpoint ?? "");
  const [bucket, setBucket] = useState(connection?.bucket ?? "");
  const [addressingStyle, setAddressingStyle] = useState<ObjectStorageAddressingStyle>(
    connection?.addressing_style ?? "auto",
  );
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteRemote, setDeleteRemote] = useState(true);
  const [confirmingCleanup, setConfirmingCleanup] = useState(false);
  const creating = connection === null;
  const activePreset = getObjectStoragePreset(presetId);
  const endpointUnresolvedPlaceholder = hasEndpointPlaceholder(endpoint);
  const changesBucket =
    !!connection && (endpoint.trim() !== connection.endpoint || bucket.trim() !== connection.bucket);
  const changesRegion = !!connection && region.trim() !== connection.region;
  const requiresRelocation =
    !!connection &&
    ((connection.in_use_object_count ?? 0) > 0 || Boolean(connection.relocating)) &&
    (changesBucket || changesRegion);
  const requiresKeys = creating || changesBucket;
  const complete = Boolean(
    region.trim() &&
      endpoint.trim() &&
      !endpointUnresolvedPlaceholder &&
      bucket.trim() &&
      (!requiresKeys || (accessKeyId.trim() && secretAccessKey.trim())),
  );

  const dirty =
    region !== (connection?.region ?? "") ||
    endpoint !== (connection?.endpoint ?? "") ||
    bucket !== (connection?.bucket ?? "") ||
    addressingStyle !== (connection?.addressing_style ?? "auto") ||
    accessKeyId !== "" ||
    secretAccessKey !== "";

  const selectPreset = (nextId: ObjectStoragePresetId) => {
    setPresetId(nextId);
    const preset = getObjectStoragePreset(nextId);
    if (nextId === "custom") {
      setAutoSyncEndpoint(false);
      if (creating && !region && !endpoint && !bucket) {
        setAddressingStyle(preset.defaultAddressingStyle);
      }
      return;
    }
    setAutoSyncEndpoint(preset.endpointTemplate.includes("{region}"));
    setRegion(preset.defaultRegion);
    setEndpoint(buildPresetEndpoint(preset, preset.defaultRegion));
    setAddressingStyle(preset.defaultAddressingStyle);
  };

  const handleRegionChange = (nextRegion: string) => {
    if (autoSyncEndpoint && activePreset.endpointTemplate.includes("{region}")) {
      const prevDerived = buildPresetEndpoint(activePreset, region);
      if (endpoint.trim() === prevDerived) {
        setEndpoint(buildPresetEndpoint(activePreset, nextRegion));
      } else {
        setAutoSyncEndpoint(false);
      }
    }
    setRegion(nextRegion);
  };

  const handleEndpointChange = (nextEndpoint: string) => {
    setAutoSyncEndpoint(false);
    setEndpoint(nextEndpoint);
  };

  const addressingStyleItems: { value: ObjectStorageAddressingStyle; label: string }[] = [
    { value: "auto", label: t("object_storage_addressing_style_auto") },
    { value: "virtual", label: t("object_storage_addressing_style_virtual") },
    { value: "path", label: t("object_storage_addressing_style_path") },
  ];

  const submit = useCallback(async (): Promise<boolean> => {
    if (!complete) return false;
    setSubmitting(true);
    setError(null);
    try {
      const saved = await API.saveObjectStorageConnection({
        kind: "s3",
        region: region.trim(),
        endpoint: endpoint.trim(),
        bucket: bucket.trim(),
        addressing_style: addressingStyle,
        access_key_id: accessKeyId.trim() || undefined,
        secret_access_key: secretAccessKey.trim() || undefined,
        delete_remote_objects: changesBucket ? deleteRemote : undefined,
      });
      onSaved(saved);
      return true;
    } catch (err) {
      setError(errMsg(err));
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [
    complete,
    region,
    endpoint,
    bucket,
    addressingStyle,
    accessKeyId,
    secretAccessKey,
    changesBucket,
    deleteRemote,
    onSaved,
  ]);

  const handleSubmit = useCallback(async (): Promise<boolean> => {
    if (!complete) return false;
    if (requiresRelocation) {
      onStartRelocation(
        {
          kind: "s3",
          region: region.trim(),
          endpoint: endpoint.trim(),
          bucket: bucket.trim(),
          addressing_style: addressingStyle,
          access_key_id: accessKeyId.trim() || undefined,
          secret_access_key: secretAccessKey.trim() || undefined,
        },
        deleteRemote,
      );
      return false;
    }
    if (changesBucket && deleteRemote) {
      setConfirmingCleanup(true);
      return false;
    }
    return submit();
  }, [
    complete,
    requiresRelocation,
    onStartRelocation,
    region,
    endpoint,
    bucket,
    addressingStyle,
    accessKeyId,
    secretAccessKey,
    changesBucket,
    deleteRemote,
    submit,
    setConfirmingCleanup,
  ]);

  const confirmCleanup = async () => {
    if (!(await submit())) setConfirmingCleanup(false);
  };

  useLeaveGuard({ dirty, saving: submitting, save: handleSubmit, discard: onCancel });
  const confirmLeave = useConfirmLeave();
  const requestClose = () => {
    if (submitting) return;
    if (dirty) confirmLeave(onCancel);
    else onCancel();
  };

  const field = (
    key: string,
    label: string,
    value: string,
    onChange: (value: string) => void,
    options: { placeholder?: string; secret?: boolean; mono?: boolean; ref?: Ref<HTMLInputElement>; hint?: string } = {},
  ) => (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={`${idPrefix}-${key}`} className={LABEL_CLS}>
        {label}
      </label>
      <Input
        mono={options.mono}
        ref={options.ref}
        id={`${idPrefix}-${key}`}
        type={options.secret ? "password" : "text"}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={options.placeholder}
      />
      {options.hint && <p className="text-xs text-warn">{options.hint}</p>}
    </div>
  );

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) requestClose();
      }}
    >
      <DialogContent size="lg" initialFocus={regionRef}>
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{creating ? t("object_storage_configure") : t("object_storage_edit_title")}</DialogTitle>
            <DialogDescription>{t("object_storage_dialog_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="@container flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <span id={`${idPrefix}-preset`} className={LABEL_CLS}>
                  {t("object_storage_preset_label")}
                </span>
                <div
                  role="group"
                  aria-labelledby={`${idPrefix}-preset`}
                  className="grid grid-cols-2 gap-1.5 @sm:grid-cols-4"
                >
                  {OBJECT_STORAGE_PRESETS.map((preset) => {
                    const selected = preset.id === presetId;
                    return (
                      <button
                        key={preset.id}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => selectPreset(preset.id)}
                        className={cn(
                          "rounded-md border px-2.5 py-1.5 text-left text-xs font-medium transition-colors duration-fast",
                          selected
                            ? "border-primary bg-primary/10 text-foreground"
                            : "border-border bg-transparent text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
                        )}
                      >
                        {t(preset.labelKey)}
                      </button>
                    );
                  })}
                </div>
                <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                  {t(activePreset.hintKey)}
                </p>
              </div>

              {field("region", t("object_storage_region"), region, handleRegionChange, {
                mono: true,
                placeholder: activePreset.regionPlaceholder,
                ref: regionRef,
              })}
              {field("endpoint", t("object_storage_endpoint"), endpoint, handleEndpointChange, {
                placeholder: activePreset.endpointPlaceholder,
                hint: endpointUnresolvedPlaceholder ? t("object_storage_endpoint_placeholder_hint") : undefined,
              })}
              {field("bucket", t("object_storage_bucket"), bucket, setBucket, {
                mono: true,
                placeholder: activePreset.bucketPlaceholder,
              })}
              <div className="flex flex-col gap-1.5">
                <span id={`${idPrefix}-addressing-style`} className={LABEL_CLS}>
                  {t("object_storage_addressing_style")}
                </span>
                <Select
                  items={addressingStyleItems}
                  value={addressingStyle}
                  onValueChange={(next) => {
                    if (next !== null) setAddressingStyle(next);
                  }}
                >
                  <SelectTrigger aria-labelledby={`${idPrefix}-addressing-style`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {addressingStyleItems.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {field("ak", t("object_storage_access_key_id"), accessKeyId, setAccessKeyId, {
                mono: true,
                secret: true,
                placeholder:
                  connection && !changesBucket
                    ? t("credential_keep_existing", { masked: connection.access_key_id_masked })
                    : undefined,
              })}
              {field("sk", t("object_storage_secret_access_key"), secretAccessKey, setSecretAccessKey, {
                mono: true,
                secret: true,
                placeholder:
                  connection && !changesBucket
                    ? t("credential_keep_existing", { masked: connection.secret_access_key_masked })
                    : undefined,
              })}
              <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
                <p className="min-w-0 flex-1">{t("object_storage_prefix_exclusive_notice")}</p>
              </div>
              {changesBucket && (
                <p className="text-xs text-muted-foreground">{t("object_storage_target_keys_required_hint")}</p>
              )}
              {changesBucket && !requiresRelocation && (
                <CleanupOptions
                  checked={deleteRemote}
                  onChange={setDeleteRemote}
                  pendingIngestTasks={pendingIngestTasks}
                  disabled={submitting}
                />
              )}
              {error && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {error}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={requestClose}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" disabled={submitting || !complete}>
              {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("common:save")}
            </Button>
          </DialogFooter>
        </form>
        <AlertDialog
          open={confirmingCleanup}
          onOpenChange={(next) => {
            if (!next && !submitting) setConfirmingCleanup(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("object_storage_relocate_confirm_title")}</AlertDialogTitle>
            </AlertDialogHeader>
            <AlertDialogBody>
              <AlertDialogDescription>{t("object_storage_relocate_confirm_desc")}</AlertDialogDescription>
            </AlertDialogBody>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void confirmCleanup()}>
                {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {t("object_storage_relocate_confirm_action")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}

function DeleteObjectStorageDialog({
  pendingIngestTasks,
  newImageTarget,
  onClose,
  onDeleted,
}: {
  pendingIngestTasks: number;
  newImageTarget: NewImageTarget;
  onClose: () => void;
  onDeleted: (result: ObjectStorageCleanupResult, resetTarget: boolean) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [deleteRemote, setDeleteRemote] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const willResetTarget = newImageTarget === "object_storage";

  const handleDelete = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const result = willResetTarget
        ? await API.deleteObjectStorageConnection(deleteRemote, { resetNewImageTarget: true })
        : await API.deleteObjectStorageConnection(deleteRemote);
      onDeleted(result, willResetTarget);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("object_storage_delete_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-2">
            <AlertDialogDescription>{t("object_storage_delete_desc")}</AlertDialogDescription>
            {willResetTarget && (
              <p className="text-sm text-muted-foreground">{t("object_storage_delete_reset_target_hint")}</p>
            )}
            <CleanupOptions
              checked={deleteRemote}
              onChange={setDeleteRemote}
              pendingIngestTasks={pendingIngestTasks}
              disabled={submitting}
            />
            {error && (
              <p role="alert" className="text-sm wrap-break-word text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void handleDelete()}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("common:delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
