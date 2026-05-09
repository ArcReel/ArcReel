import { useState, useEffect, useCallback, useRef, memo } from "react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { errMsg, voidPromise } from "@/utils/async";
import {
  Check,
  Edit2,
  Loader2,
  Plus,
  Trash2,
  Upload,
  Wifi,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import {
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import type { ProviderCredential, ProviderTestResult } from "@/types";

const PRIMARY_BTN_CLS =
  "inline-flex items-center gap-1.5 rounded-[8px] px-3 py-1.5 text-[12px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50";

const ICON_BTN_CLS =
  "rounded-[5px] p-1.5 text-text-4 transition-colors hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

interface RowProps {
  cred: ProviderCredential;
  providerId: string;
  isVertex: boolean;
  onChanged: () => void;
}

const CredentialRow = memo(function CredentialRow({ cred, providerId, isVertex, onChanged }: RowProps) {
  const { t } = useTranslation("dashboard");
  const [editing, setEditing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState({ name: cred.name, api_key: "", base_url: cred.base_url ?? "" });

  const handleActivate = useCallback(async () => {
    try {
      await API.activateCredential(providerId, cred.id);
      onChanged();
    } catch {
      // 网络错误静默处理
    }
  }, [providerId, cred.id, onChanged]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await API.testProviderConnection(providerId, cred.id);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, available_models: [], message: errMsg(e) });
    }
    setTesting(false);
  }, [providerId, cred.id]);

  const handleDelete = useCallback(async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    try {
      await API.deleteCredential(providerId, cred.id);
      onChanged();
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [providerId, cred.id, confirmDelete, onChanged]);

  const handleSaveEdit = useCallback(async () => {
    const data: Record<string, string> = {};
    if (draft.name && draft.name !== cred.name) data.name = draft.name;
    if (draft.api_key) data.api_key = draft.api_key;
    if (draft.base_url !== (cred.base_url ?? "")) data.base_url = draft.base_url;
    if (Object.keys(data).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await API.updateCredential(providerId, cred.id, data);
      setEditing(false);
      onChanged();
    } finally {
      setSaving(false);
    }
  }, [draft, cred, providerId, onChanged]);

  const editPrefix = `cred-edit-${cred.id}`;

  return (
    <div
      className="relative rounded-[8px] border border-hairline px-3 py-2.5 transition-colors hover:border-hairline-strong"
      style={
        cred.is_active
          ? {
              ...CARD_STYLE,
              boxShadow:
                "inset 2px 0 0 var(--color-accent), 0 0 18px -10px var(--color-accent-glow)",
            }
          : undefined
      }
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={cred.is_active ? undefined : voidPromise(handleActivate)}
          disabled={cred.is_active}
          aria-label={cred.is_active ? t("currently_active") : t("activate_credential", { name: cred.name })}
          className={`h-2.5 w-2.5 flex-shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
            cred.is_active
              ? ""
              : "border border-hairline-strong hover:border-accent-2 cursor-pointer"
          }`}
          style={
            cred.is_active
              ? {
                  background: "var(--color-accent)",
                  boxShadow: "0 0 8px var(--color-accent-glow)",
                }
              : undefined
          }
        />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-text">{cred.name}</span>
            {cred.is_active && (
              <span
                className="rounded-full px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em]"
                style={{
                  background: "var(--color-accent-dim)",
                  color: "var(--color-accent-2)",
                  border: "1px solid var(--color-accent-soft)",
                }}
              >
                {t("active_label")}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2">
            {cred.api_key_masked && (
              <span className="font-mono text-[11px] text-text-4">{cred.api_key_masked}</span>
            )}
            {cred.credentials_filename && (
              <span className="text-[11px] text-text-4">{cred.credentials_filename}</span>
            )}
          </div>
          {cred.base_url && (
            <div className="mt-0.5 truncate font-mono text-[10.5px] text-text-4">{cred.base_url}</div>
          )}
        </div>

        <div className="flex flex-shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={voidPromise(handleTest)}
            disabled={testing}
            aria-label={t("test_credential", { name: cred.name })}
            className={ICON_BTN_CLS}
          >
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
            ) : (
              <Wifi className="h-3.5 w-3.5" />
            )}
          </button>
          {!isVertex && (
            <button
              type="button"
              onClick={() => {
                setEditing(!editing);
                setDraft({ name: cred.name, api_key: "", base_url: cred.base_url ?? "" });
                setTestResult(null);
              }}
              aria-label={t("edit_credential", { name: cred.name })}
              className={ICON_BTN_CLS}
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
          )}
          {!confirmDelete ? (
            <button
              type="button"
              onClick={voidPromise(handleDelete)}
              disabled={deleting}
              aria-label={t("delete_credential", { name: cred.name })}
              className={`${ICON_BTN_CLS} hover:text-warm-bright`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={voidPromise(handleDelete)}
                disabled={deleting}
                className="inline-flex items-center gap-1 rounded-[6px] px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                style={{
                  background: "var(--color-warm-tint)",
                  color: "var(--color-warm-bright)",
                  border: "1px solid var(--color-warm-ring)",
                }}
              >
                {deleting ? (
                  <Loader2 className="h-3 w-3 motion-safe:animate-spin" />
                ) : (
                  t("common:confirm")
                )}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {t("common:cancel")}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <div
          aria-live="polite"
          className="mt-2 ml-5.5 rounded-[8px] px-3 py-2 text-[12px]"
          style={
            testResult.success
              ? {
                  background: "oklch(0.30 0.10 155 / 0.15)",
                  color: "var(--color-good)",
                  border: "1px solid oklch(0.45 0.10 155 / 0.30)",
                }
              : {
                  background: "var(--color-warm-tint)",
                  color: "var(--color-warm-bright)",
                  border: "1px solid var(--color-warm-ring)",
                }
          }
        >
          {testResult.message}
          {testResult.success && testResult.available_models.length > 0 && (
            <div className="mt-1 opacity-75">
              {t("available_models")}{testResult.available_models.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Inline edit */}
      {editing && (
        <div
          className="mt-2.5 ml-5.5 space-y-2.5 rounded-[8px] border border-hairline p-3"
          style={CARD_STYLE}
        >
          <div>
            <FieldLabel htmlFor={`${editPrefix}-name`}>{t("credential_name")}</FieldLabel>
            <input
              id={`${editPrefix}-name`}
              name="name"
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              className={INPUT_CLS}
            />
          </div>
          <div>
            <FieldLabel htmlFor={`${editPrefix}-apikey`}>{t("api_key_keep_hint")}</FieldLabel>
            <input
              id={`${editPrefix}-apikey`}
              name="api_key"
              type="password"
              autoComplete="off"
              value={draft.api_key}
              onChange={(e) => setDraft((d) => ({ ...d, api_key: e.target.value }))}
              placeholder={t("keep_existing_placeholder")}
              className={INPUT_CLS}
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <FieldLabel htmlFor={`${editPrefix}-baseurl`}>{t("base_url_optional")}</FieldLabel>
              <input
                id={`${editPrefix}-baseurl`}
                name="base_url"
                type="url"
                value={draft.base_url}
                onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
                placeholder={t("default_url_placeholder")}
                className={INPUT_CLS}
              />
            </div>
          )}
          <div className="flex gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => void handleSaveEdit()}
              disabled={saving}
              className={PRIMARY_BTN_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {saving ? (
                <Loader2 className="h-3 w-3 motion-safe:animate-spin" />
              ) : (
                <Check className="h-3 w-3" />
              )}
              {t("common:save")}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className={GHOST_BTN_CLS}
            >
              <X className="h-3 w-3" /> {t("common:cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

interface AddFormProps {
  providerId: string;
  isVertex: boolean;
  onCreated: () => void;
  onCancel: () => void;
}

function AddCredentialForm({ providerId, isVertex, onCreated, onCancel }: AddFormProps) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useAutoFocus<HTMLInputElement>();

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (isVertex) {
        const file = fileRef.current?.files?.[0];
        if (!file) {
          setError(t("select_credential_file"));
          setSaving(false);
          return;
        }
        await API.uploadVertexCredential(name, file);
      } else {
        if (!apiKey.trim()) {
          setError(t("enter_api_key_required"));
          setSaving(false);
          return;
        }
        await API.createCredential(providerId, {
          name: name.trim(),
          api_key: apiKey || undefined,
          base_url: baseUrl || undefined,
        });
      }
      onCreated();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="space-y-2.5 rounded-[8px] border border-hairline p-3"
      style={CARD_STYLE}
    >
      <div>
        <FieldLabel htmlFor="cred-add-name" required>
          {t("credential_name")}
        </FieldLabel>
        <input
          id="cred-add-name"
          name="name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("credential_name_placeholder")}
          className={INPUT_CLS}
          ref={nameRef}
        />
      </div>
      {isVertex ? (
        <div>
          <FieldLabel htmlFor="cred-add-file" required>
            {t("credential_file")}
          </FieldLabel>
          <button
            id="cred-add-file"
            type="button"
            onClick={() => fileRef.current?.click()}
            className={GHOST_BTN_CLS}
          >
            <Upload className="h-3 w-3" />
            {fileRef.current?.files?.[0]?.name ?? t("select_json_file")}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            aria-label={t("import_credential_file_aria")}
            className="hidden"
            onChange={() => setError(null)}
          />
        </div>
      ) : (
        <>
          <div>
            <FieldLabel htmlFor="cred-add-apikey" required>
              API Key
            </FieldLabel>
            <input
              id="cred-add-apikey"
              name="api_key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={INPUT_CLS}
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <FieldLabel htmlFor="cred-add-baseurl">{t("base_url_optional")}</FieldLabel>
              <input
                id="cred-add-baseurl"
                name="base_url"
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={t("default_url_placeholder")}
                className={INPUT_CLS}
              />
            </div>
          )}
        </>
      )}
      {error && (
        <p
          className="rounded-[6px] px-2.5 py-1.5 text-[11.5px]"
          aria-live="polite"
          style={{
            background: "var(--color-warm-tint)",
            color: "var(--color-warm-bright)",
            border: "1px solid var(--color-warm-ring)",
          }}
        >
          {error}
        </p>
      )}
      <div className="flex gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={saving || !name.trim()}
          className={PRIMARY_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {saving ? (
            <Loader2 className="h-3 w-3 motion-safe:animate-spin" />
          ) : (
            <Plus className="h-3 w-3" />
          )}
          {t("add")}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={GHOST_BTN_CLS}
        >
          {t("common:cancel")}
        </button>
      </div>
    </div>
  );
}

interface Props {
  providerId: string;
  onChanged?: () => void;
}

export function CredentialList({ providerId, onChanged }: Props) {
  const { t } = useTranslation("dashboard");
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const isVertex = providerId === "gemini-vertex";

  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  const refresh = useCallback(async () => {
    try {
      const { credentials: creds } = await API.listCredentials(providerId);
      setCredentials(creds);
    } finally {
      setLoading(false);
    }
  }, [providerId]);

  const handleChanged = useCallback(async () => {
    await refresh();
    onChangedRef.current?.();
  }, [refresh]);

  useEffect(() => {
    setLoading(true);
    setShowAdd(false);
    void refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {t("credential_mgmt")}
        </div>
        {!showAdd && (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="inline-flex items-center gap-1 rounded-[6px] px-2 py-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2 transition-colors hover:bg-accent-dim hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Plus className="h-3 w-3" /> {t("add_credential")}
          </button>
        )}
      </div>

      {credentials.length === 0 && !showAdd && (
        <div className="rounded-[10px] border border-dashed border-hairline-strong bg-bg-grad-a/45 px-4 py-7 text-center">
          <p className="text-[12.5px] text-text-3">{t("no_credentials")}</p>
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="mt-2 inline-flex items-center gap-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2 transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Plus className="h-3 w-3" /> {t("add_first_credential")}
          </button>
        </div>
      )}

      <div className="space-y-1.5">
        {credentials.map((c) => (
          <CredentialRow
            key={c.id}
            cred={c}
            providerId={providerId}
            isVertex={isVertex}
            onChanged={voidPromise(handleChanged)}
          />
        ))}
      </div>

      {showAdd && (
        <div className="mt-3">
          <AddCredentialForm
            providerId={providerId}
            isVertex={isVertex}
            onCreated={() => {
              setShowAdd(false);
              void handleChanged();
            }}
            onCancel={() => setShowAdd(false)}
          />
        </div>
      )}
    </div>
  );
}
