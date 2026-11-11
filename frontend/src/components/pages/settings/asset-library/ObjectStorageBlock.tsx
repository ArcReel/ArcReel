import { useCallback, useEffect, useId, useRef, useState, type Ref } from "react";
import { Loader2, Pencil, Plus, Trash2, Wifi } from "lucide-react";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
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
import type {
  NewImageTarget,
  ObjectStorageConnection,
  ObjectStorageSettings,
  ObjectStorageCleanupResult,
  SavedObjectStorageConnection,
} from "@/types";
import { errMsg } from "@/utils/async";

const LABEL_CLS = "text-sm font-medium text-foreground";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; settings: ObjectStorageSettings };

type TestState = { status: "idle" } | { status: "testing" } | { status: "ok" } | { status: "failed"; error: unknown };

/**
 * 「对象存储」块：查看、配置、测试与删除实例唯一的 TOS 连接，并选择新增图片的保存位置。
 * 连接在对话框里提交；保存位置选中即保存。两者都立即生效。
 */
export function ObjectStorageBlock() {
  const { t } = useTranslation(["dashboard", "common"]);
  const headingId = useId();
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [cleanupFailures, setCleanupFailures] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
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

  // 连接被替换或删除后，在途的测试结果属于旧连接，不再落地
  const testGeneration = useRef(0);
  const resetTest = useCallback(() => {
    testGeneration.current += 1;
    setTest({ status: "idle" });
  }, []);
  const closeEditor = useCallback(() => setEditing(false), []);
  const patchSettings = useCallback((patch: Partial<ObjectStorageSettings>) => {
    setLoad((prev) => (prev.status === "ready" ? { status: "ready", settings: { ...prev.settings, ...patch } } : prev));
  }, []);
  // 页面可能打开很久：对话框打开时取一次最新的入库任务数，取不到时保留已有数量，不打断编辑
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
      setEditing(false);
      resetTest();
      patchSettings({ connection: saved });
      void fetchConnection();
    },
    [resetTest, patchSettings, fetchConnection],
  );
  const handleTargetSaved = useCallback(
    (target: NewImageTarget) => patchSettings({ new_image_target: target }),
    [patchSettings],
  );
  const runTest = async () => {
    const generation = ++testGeneration.current;
    setTest({ status: "testing" });
    try {
      await API.checkObjectStorageConnection();
      if (generation === testGeneration.current) setTest({ status: "ok" });
    } catch (error) {
      if (generation === testGeneration.current) setTest({ status: "failed", error });
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
          onTest={() => void runTest()}
          onEdit={() => {
            setEditing(true);
            void refreshPendingIngest();
          }}
          onDelete={() => {
            setDeleting(true);
            void refreshPendingIngest();
          }}
        />
      )}

      {settings && (
        <NewImageTargetField
          target={settings.new_image_target}
          connectionConfigured={connection !== null}
          onSaved={handleTargetSaved}
        />
      )}

      {editing && (
        <ObjectStorageDialog
          connection={connection}
          pendingIngestTasks={settings?.pending_ingest_tasks ?? 0}
          onCancel={closeEditor}
          onSaved={handleSaved}
        />
      )}
      {deleting && (
        <DeleteObjectStorageDialog
          pendingIngestTasks={settings?.pending_ingest_tasks ?? 0}
          onClose={() => setDeleting(false)}
          onDeleted={(result) => {
            setDeleting(false);
            resetTest();
            setCleanupFailures(result.cleanup_failures);
            patchSettings({ connection: null, pending_ingest_tasks: 0 });
          }}
        />
      )}
      <CleanupFailures failures={cleanupFailures} />
    </section>
  );
}

/** 已配置连接的摘要：桶、Endpoint、区域与掩码后的 Access Key ID，以及测试、编辑与删除入口。 */
function ConnectionCard({
  connection,
  test,
  onTest,
  onEdit,
  onDelete,
}: {
  connection: ObjectStorageConnection;
  test: TestState;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2.5">
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
      <div aria-live="polite">
        {test.status === "ok" && <p className="text-xs text-good">{t("object_storage_test_ok")}</p>}
        {test.status === "failed" && <RequestErrorNotice error={test.error} className="text-xs" />}
      </div>
    </div>
  );
}

/**
 * 新增图片保存位置：选中即保存，只影响之后提交的图片内容版本。没有连接时不能选对象存储
 * （只配置连接用于中转，不等于启用远端保存）。
 */
function NewImageTargetField({
  target,
  connectionConfigured,
  onSaved,
}: {
  target: NewImageTarget;
  connectionConfigured: boolean;
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
      hint: connectionConfigured ? t("new_image_target_object_storage_hint") : t("new_image_target_needs_connection"),
      // 已选中时保持可见可读：连接在别处被删掉的少见情况下，仍能看出当前设置并改回本地
      disabled: !connectionConfigured && target !== "object_storage",
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
}: {
  connection: ObjectStorageConnection | null;
  pendingIngestTasks: number;
  onCancel: () => void;
  onSaved: (connection: SavedObjectStorageConnection) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idPrefix = useId();
  const regionRef = useRef<HTMLInputElement>(null);
  const [region, setRegion] = useState(connection?.region ?? "");
  const [endpoint, setEndpoint] = useState(connection?.endpoint ?? "");
  const [bucket, setBucket] = useState(connection?.bucket ?? "");
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteRemote, setDeleteRemote] = useState(true);
  const [confirmingCleanup, setConfirmingCleanup] = useState(false);
  const creating = connection === null;
  const changesBucket =
    !!connection && (endpoint.trim() !== connection.endpoint || bucket.trim() !== connection.bucket);
  const complete =
    region.trim() && endpoint.trim() && bucket.trim() && (!creating || (accessKeyId.trim() && secretAccessKey.trim()));

  const dirty =
    region !== (connection?.region ?? "") ||
    endpoint !== (connection?.endpoint ?? "") ||
    bucket !== (connection?.bucket ?? "") ||
    accessKeyId !== "" ||
    secretAccessKey !== "";

  const submit = useCallback(async (): Promise<boolean> => {
    if (!complete) return false;
    setSubmitting(true);
    setError(null);
    try {
      const saved = await API.saveObjectStorageConnection({
        region: region.trim(),
        endpoint: endpoint.trim(),
        bucket: bucket.trim(),
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
  }, [complete, region, endpoint, bucket, accessKeyId, secretAccessKey, changesBucket, deleteRemote, onSaved]);

  // 保存会立即删除旧桶里的文件，不可恢复，须先确认。「保存并离开」也走这里：确认前留在原处，不算保存成功
  const handleSubmit = useCallback(async (): Promise<boolean> => {
    if (!complete) return false;
    if (changesBucket && deleteRemote) {
      setConfirmingCleanup(true);
      return false;
    }
    return submit();
  }, [complete, changesBucket, deleteRemote, submit, setConfirmingCleanup]);

  const confirmCleanup = async () => {
    if (!(await submit())) setConfirmingCleanup(false);
  };

  // 未保存的填写登记到离开拦截；关闭对话框也经它询问，不静默丢弃
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
    options: { placeholder?: string; secret?: boolean; ref?: Ref<HTMLInputElement> } = {},
  ) => (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={`${idPrefix}-${key}`} className={LABEL_CLS}>
        {label}
      </label>
      <Input
        mono
        ref={options.ref}
        id={`${idPrefix}-${key}`}
        type={options.secret ? "password" : "text"}
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={options.placeholder}
      />
    </div>
  );

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) requestClose();
      }}
    >
      <DialogContent initialFocus={regionRef}>
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
            <div className="flex flex-col gap-4">
              {field("region", t("object_storage_region"), region, setRegion, {
                placeholder: "cn-beijing",
                ref: regionRef,
              })}
              {field("endpoint", t("object_storage_endpoint"), endpoint, setEndpoint, {
                placeholder: "tos-cn-beijing.volces.com",
              })}
              {field("bucket", t("object_storage_bucket"), bucket, setBucket)}
              {field("ak", t("object_storage_access_key_id"), accessKeyId, setAccessKeyId, {
                secret: true,
                placeholder: connection
                  ? t("credential_keep_existing", { masked: connection.access_key_id_masked })
                  : undefined,
              })}
              {field("sk", t("object_storage_secret_access_key"), secretAccessKey, setSecretAccessKey, {
                secret: true,
                placeholder: connection
                  ? t("credential_keep_existing", { masked: connection.secret_access_key_masked })
                  : undefined,
              })}
              {changesBucket && (
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
  onClose,
  onDeleted,
}: {
  pendingIngestTasks: number;
  onClose: () => void;
  onDeleted: (result: ObjectStorageCleanupResult) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [deleteRemote, setDeleteRemote] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await API.deleteObjectStorageConnection(deleteRemote);
      onDeleted(result);
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
