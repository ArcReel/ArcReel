import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Check, Loader2, MoreHorizontal, Pencil, Plus, Trash2, Upload, Wifi } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "cn";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
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
import { Badge } from "@/components/ui/badge";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { CredentialSecretField, ProviderCredential, ConnectivityCheckResult } from "@/types";

// Known secret credential fields → frontend i18n label keys; unknown keys use backend labels.
const SECRET_FIELD_LABEL_KEY: Record<string, string> = {
  api_key: "api_key_label",
  access_key: "access_key_label",
  secret_key: "secret_key_label",
};

function secretFieldLabel(t: TFunction, field: CredentialSecretField): string {
  const lk = SECRET_FIELD_LABEL_KEY[field.key];
  return lk ? t(lk) : field.label;
}

/** Dialog secret labels: one secret field uses Key; multiple fields (Kling) retain vendor-specific names. */
function secretInputLabel(t: TFunction, field: CredentialSecretField, fieldCount: number): string {
  return fieldCount === 1 ? t("credential_secret_label") : secretFieldLabel(t, field);
}

// Read redacted field values individually, matching backend *_masked columns.
function maskedForKey(cred: ProviderCredential, key: string): string | null | undefined {
  if (key === "api_key") return cred.api_key_masked;
  if (key === "access_key") return cred.access_key_masked;
  if (key === "secret_key") return cred.secret_key_masked;
  return undefined;
}

const LABEL_CLS = "text-sm font-medium text-foreground";

interface Props {
  providerId: string;
  supportsBaseUrl: boolean;
  secretFields: CredentialSecretField[];
  /** Alternative credential groups: any fully filled group completes credentials; one group means all required. */
  secretFieldGroups: string[][];
  /** Called after credential additions/changes/deletions or active-key switches, letting parents refresh status/catalogs. */
  onChanged?: () => void;
}

type DialogState = { mode: "add" } | { mode: "edit"; cred: ProviderCredential } | null;

/**
 * Built-in provider Keys sections. Add/edit through dialogs, delete after AlertDialog confirmation and switch active keys;
 * all apply immediately, bypassing detail-bottom save bars, which handle advanced configuration only.
 */
export function CredentialList({ providerId, supportsBaseUrl, secretFields, secretFieldGroups, onChanged }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<ProviderCredential | null>(null);
  const headingId = useId();
  const isVertex = providerId === "gemini-vertex";

  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

  const loadController = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const { credentials: creds } = await API.listCredentials(providerId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setCredentials(creds);
      setLoadError(null);
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoadError(errMsg(err));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [providerId]);

  const handleChanged = useCallback(async () => {
    await load();
    onChangedRef.current?.();
  }, [load]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- asynchronously load on mount/provider changes; cancellation domains cover later immediate-action refreshes
    void load();
    return () => loadController.current?.abort();
  }, [load]);

  const activate = useCallback(
    async (cred: ProviderCredential) => {
      try {
        await API.activateCredential(providerId, cred.id);
        await handleChanged();
      } catch (err) {
        pushToast(errMsg(err), "error");
      }
    },
    [providerId, pushToast, handleChanged],
  );

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h3 id={headingId} className="text-base font-medium">
          {t("provider_credentials_title")}
        </h3>
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => setDialog({ mode: "add" })}>
          <Plus data-icon="inline-start" />
          {t("add_key")}
        </Button>
      </div>

      {loading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      ) : loadError ? (
        <div role="alert" className="flex items-center gap-3 text-sm text-warn">
          <span className="min-w-0 flex-1 wrap-break-word">{loadError}</span>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            {t("common:retry")}
          </Button>
        </div>
      ) : credentials.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {t("no_credentials")}
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {credentials.map((cred) => (
            <CredentialRow
              key={cred.id}
              cred={cred}
              providerId={providerId}
              secretFields={secretFields}
              canEdit={!isVertex}
              onActivate={activate}
              onEdit={() => setDialog({ mode: "edit", cred })}
              onDelete={() => setDeleting(cred)}
            />
          ))}
        </ul>
      )}

      {dialog && (
        <CredentialDialog
          // Every opening creates fresh forms; closing/reopening retains no previous inputs/errors.
          key={dialog.mode === "edit" ? dialog.cred.id : "add"}
          cred={dialog.mode === "edit" ? dialog.cred : null}
          providerId={providerId}
          isVertex={isVertex}
          supportsBaseUrl={supportsBaseUrl}
          secretFields={secretFields}
          secretFieldGroups={secretFieldGroups}
          onDone={() => {
            setDialog(null);
            void handleChanged();
          }}
          onCancel={() => setDialog(null)}
        />
      )}

      <DeleteCredentialDialog
        cred={deleting}
        providerId={providerId}
        onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null);
          void handleChanged();
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// List rows.
// ---------------------------------------------------------------------------

interface RowProps {
  cred: ProviderCredential;
  providerId: string;
  secretFields: CredentialSecretField[];
  /** Vertex credentials are uploaded JSON files, replaceable only through deletion/reupload. */
  canEdit: boolean;
  onActivate: (cred: ProviderCredential) => Promise<void>;
  onEdit: () => void;
  onDelete: () => void;
}

function CredentialRow({ cred, providerId, secretFields, canEdit, onActivate, onEdit, onDelete }: RowProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [activating, setActivating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectivityCheckResult | null>(null);

  const masked = secretFields
    .map((field) => ({ field, value: maskedForKey(cred, field.key) }))
    .filter((entry): entry is { field: CredentialSecretField; value: string } => Boolean(entry.value));

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await API.checkProviderConnectivity(providerId, cred.id));
    } catch (err) {
      setTestResult({ success: false, available_models: [], message: errMsg(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <li className="flex flex-col gap-2 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-pressed={cred.is_active}
          aria-label={cred.is_active ? t("currently_active") : t("activate_credential", { name: cred.name })}
          disabled={cred.is_active || activating}
          onClick={() => {
            setActivating(true);
            void onActivate(cred).finally(() => setActivating(false));
          }}
          className="group flex size-6 shrink-0 items-center justify-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <span
            aria-hidden
            className={cn(
              "flex size-4 items-center justify-center rounded-full border transition-colors",
              cred.is_active
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input group-hover:border-subtle-foreground",
            )}
          >
            {cred.is_active && <Check className="size-3" />}
          </span>
        </button>

        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex min-w-0 items-center gap-2">
            <TruncatedText text={cred.name} className="text-sm font-medium" />
            {cred.is_active && <Badge variant="secondary">{t("active_label")}</Badge>}
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
            {masked.map(({ field, value }) => (
              <span key={field.key} className="font-mono">
                {secretFields.length > 1 ? `${secretFieldLabel(t, field)}: ${value}` : value}
              </span>
            ))}
            {cred.credentials_filename && <span>{cred.credentials_filename}</span>}
          </div>
          {cred.base_url && <TruncatedText text={cred.base_url} className="font-mono text-xs text-muted-foreground" />}
        </div>

        <Button
          variant="ghost"
          size="sm"
          disabled={testing}
          aria-label={t("check_credential_connectivity", { name: cred.name })}
          onClick={() => void handleTest()}
        >
          {testing ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <Wifi aria-hidden data-icon="inline-start" />
          )}
          {t("test_credential")}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label={t("credential_more_actions", { name: cred.name })} />
            }
          >
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {canEdit && (
              <>
                <DropdownMenuItem onClick={onEdit}>
                  <Pencil />
                  {t("common:edit")}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            <DropdownMenuItem variant="destructive" onClick={onDelete}>
              <Trash2 />
              {t("common:delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {testResult && (
        <div
          aria-live="polite"
          className={cn("pl-8 text-xs wrap-break-word", testResult.success ? "text-good" : "text-destructive")}
        >
          <p>{testResult.message}</p>
          {testResult.success && testResult.available_models.length > 0 && (
            <p className="text-muted-foreground">
              {t("available_models")}
              {testResult.available_models.join(", ")}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Add/edit dialogs
// ---------------------------------------------------------------------------

interface CredentialDialogProps {
  /** Edited credentials; null when adding. */
  cred: ProviderCredential | null;
  providerId: string;
  isVertex: boolean;
  supportsBaseUrl: boolean;
  secretFields: CredentialSecretField[];
  secretFieldGroups: string[][];
  onDone: () => void;
  onCancel: () => void;
}

function CredentialDialog({
  cred,
  providerId,
  isVertex,
  supportsBaseUrl,
  secretFields,
  secretFieldGroups,
  onDone,
  onCancel,
}: CredentialDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idPrefix = useId();
  const editing = cred !== null;
  const [name, setName] = useState(cred?.name ?? "");
  // Blank secrets during edits retain existing values independently per field.
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [baseUrl, setBaseUrl] = useState(cred?.base_url ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const fieldByKey = new Map(secretFields.map((f) => [f.key, f]));
  const labelForKey = (key: string) => secretFieldLabel(t, fieldByKey.get(key) ?? { key, label: key });
  const groups = secretFieldGroups.length > 0 ? secretFieldGroups : [secretFields.map((f) => f.key)];
  // Only single required groups have unconditionally required fields; alternative groups use combination hints, not required markers.
  const fieldsRequired = !editing && groups.length <= 1;
  const orHint =
    !editing && groups.length > 1
      ? groups.map((g) => g.map(labelForKey).join(" + ")).join(` ${t("or_label")} `)
      : null;

  const submitAdd = async () => {
    if (isVertex) {
      if (!file) {
        setError(t("select_credential_file"));
        return false;
      }
      await API.uploadVertexCredential(name.trim(), file);
      return true;
    }
    const groupSatisfied = (group: string[]) => group.every((k) => (secrets[k] ?? "").trim());
    if (!groups.some(groupSatisfied)) {
      setError(groups.length > 1 ? t("enter_credentials_required_any_group") : t("enter_credentials_required"));
      return false;
    }
    const payload: { name: string; [key: string]: string | undefined } = {
      name: name.trim(),
      base_url: baseUrl.trim() || undefined,
    };
    for (const field of secretFields) payload[field.key] = secrets[field.key]?.trim() || undefined;
    await API.createCredential(providerId, payload);
    return true;
  };

  const submitEdit = async (current: ProviderCredential) => {
    const data: Record<string, string> = {};
    if (name.trim() !== current.name) data.name = name.trim();
    for (const field of secretFields) {
      // Whitespace-only inputs are not new values and never overwrite saved secrets.
      const value = secrets[field.key]?.trim();
      if (value) data[field.key] = value;
    }
    if (baseUrl.trim() !== (current.base_url ?? "")) data.base_url = baseUrl.trim();
    if (Object.keys(data).length > 0) await API.updateCredential(providerId, current.id, data);
    return true;
  };

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const done = cred ? await submitEdit(cred) : await submitAdd();
      if (done) onDone();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        // In-flight submissions ignore Esc/backdrop clicks, preventing dialogs disappearing before results.
        if (!open && !submitting) onCancel();
      }}
    >
      <DialogContent initialFocus={nameRef}>
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{editing ? t("credential_edit_title") : t("credential_add_title")}</DialogTitle>
            <DialogDescription>{t("credential_dialog_description")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`${idPrefix}-name`} className={LABEL_CLS}>
                  {t("credential_name")}
                </label>
                <Input
                  id={`${idPrefix}-name`}
                  ref={nameRef}
                  required
                  autoComplete="off"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("credential_name_placeholder")}
                />
              </div>

              {isVertex && !editing ? (
                <div className="flex flex-col gap-1.5">
                  <span className={LABEL_CLS}>{t("credential_file")}</span>
                  <Button variant="outline" className="self-start" onClick={() => fileRef.current?.click()}>
                    <Upload data-icon="inline-start" />
                    {file?.name ?? t("select_json_file")}
                  </Button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".json,application/json"
                    aria-label={t("import_credential_file_aria")}
                    className="hidden"
                    onChange={(e) => {
                      setError(null);
                      setFile(e.currentTarget.files?.[0] ?? null);
                    }}
                  />
                </div>
              ) : (
                <>
                  {orHint && <p className="text-xs text-muted-foreground">{orHint}</p>}
                  {secretFields.map((field) => {
                    const current = cred ? maskedForKey(cred, field.key) : null;
                    return (
                      <div key={field.key} className="flex flex-col gap-1.5">
                        <label htmlFor={`${idPrefix}-${field.key}`} className={LABEL_CLS}>
                          {secretInputLabel(t, field, secretFields.length)}
                        </label>
                        <Input
                          mono
                          id={`${idPrefix}-${field.key}`}
                          type="password"
                          autoComplete="off"
                          required={fieldsRequired}
                          value={secrets[field.key] ?? ""}
                          onChange={(e) => setSecrets((s) => ({ ...s, [field.key]: e.target.value }))}
                          placeholder={current ? t("credential_keep_existing", { masked: current }) : undefined}
                        />
                      </div>
                    );
                  })}
                  {supportsBaseUrl && (
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor={`${idPrefix}-base-url`} className={LABEL_CLS}>
                        {t("base_url_optional")}
                      </label>
                      <Input
                        id={`${idPrefix}-base-url`}
                        type="url"
                        autoComplete="off"
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder={t("default_url_placeholder")}
                      />
                    </div>
                  )}
                </>
              )}

              {error && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {error}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={onCancel}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" disabled={submitting || !name.trim()}>
              {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {editing ? t("common:save") : t("add_key")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation
// ---------------------------------------------------------------------------

function DeleteCredentialDialog({
  cred,
  providerId,
  onClose,
  onDeleted,
}: {
  cred: ProviderCredential | null;
  providerId: string;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // During close animations cred is cleared; retain prior contents so titles never flash blank.
  const [shown, setShown] = useState(cred);
  if (cred && cred !== shown) {
    setShown(cred);
    setError(null);
  }

  const handleDelete = async () => {
    if (!cred) return;
    setSubmitting(true);
    setError(null);
    try {
      await API.deleteCredential(providerId, cred.id);
      onDeleted();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={cred !== null}
      onOpenChange={(open) => {
        // In-flight deletion ignores Esc.
        if (!open && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("credential_delete_title", { name: shown?.name ?? "" })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("credential_delete_title", { name: shown?.name ?? "" })}>
          <div className="flex flex-col gap-2">
            <AlertDialogDescription>
              {shown?.is_active ? t("credential_delete_active_description") : t("credential_delete_description")}
            </AlertDialogDescription>
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
