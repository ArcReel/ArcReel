import { useState, useEffect, useCallback, useRef, memo } from "react";
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
import type { ProviderCredential, ProviderTestResult } from "@/types";

const focusRing = "focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:outline-none";
const inputCls = "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60";
const inputClsPlaceholder = `${inputCls} placeholder:text-gray-600`;
const primaryBtnCls = `inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-indigo-500 disabled:opacity-50 ${focusRing}`;

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
      // 网络错误静默处理，用户可重试
    }
  }, [providerId, cred.id, onChanged]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await API.testProviderConnection(providerId, cred.id);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, available_models: [], message: String(e) });
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
      className={`rounded-lg border-l-2 px-3 py-2.5 transition-colors ${
        cred.is_active
          ? "border-l-[var(--neon-500)] bg-gray-900/30"
          : "border-l-transparent hover:bg-gray-800/20"
      }`}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={cred.is_active ? undefined : handleActivate}
          disabled={cred.is_active}
          aria-label={cred.is_active ? t("currently_active") : t("activate_credential", { name: cred.name })}
          className={`h-2.5 w-2.5 flex-shrink-0 rounded-full transition-colors ${focusRing} ${
            cred.is_active
              ? "bg-[var(--neon-500)] shadow-[0_0_6px_var(--neon-500)]"
              : "border border-gray-600 hover:border-gray-400 cursor-pointer"
          }`}
        />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-200">{cred.name}</span>
            {cred.is_active && (
              <span className="rounded bg-[var(--neon-500)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--neon-500)]">
                {t("active_label")}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2">
            {cred.api_key_masked && (
              <span className="font-mono text-xs text-gray-500">{cred.api_key_masked}</span>
            )}
            {cred.credentials_filename && (
              <span className="text-xs text-gray-500">{cred.credentials_filename}</span>
            )}
          </div>
          {cred.base_url && (
            <div className="mt-0.5 truncate text-xs text-gray-600">{cred.base_url}</div>
          )}
        </div>

        <div className="flex flex-shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing}
            aria-label={t("test_credential", { name: cred.name })}
            className={`rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300 ${focusRing}`}
          >
            {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
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
              className={`rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300 ${focusRing}`}
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
          )}
          {!confirmDelete ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              aria-label={t("delete_credential", { name: cred.name })}
              className={`rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-rose-400 ${focusRing}`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className={`rounded px-2 py-1 text-xs text-rose-400 transition-colors hover:bg-rose-900/20 ${focusRing}`}
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : t("common:confirm")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className={`rounded px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300 ${focusRing}`}
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
          className={`mt-2 ml-5.5 rounded-md px-3 py-2 text-xs ${
            testResult.success
              ? "bg-green-900/20 text-green-400"
              : "bg-rose-900/15 text-rose-400"
          }`}
        >
          {testResult.message}
          {testResult.success && testResult.available_models.length > 0 && (
            <div className="mt-1 opacity-70">
              {t("available_models")}{testResult.available_models.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Inline edit */}
      {editing && (
        <div className="mt-2.5 ml-5.5 space-y-2.5 rounded-lg border border-gray-800 bg-gray-950/60 p-3">
          <div>
            <label htmlFor={`${editPrefix}-name`} className="mb-1 block text-xs text-gray-500">{t("credential_name")}</label>
            <input
              id={`${editPrefix}-name`}
              name="name"
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              className={inputCls}
            />
          </div>
          <div>
            <label htmlFor={`${editPrefix}-apikey`} className="mb-1 block text-xs text-gray-500">{t("api_key_keep_hint")}</label>
            <input
              id={`${editPrefix}-apikey`}
              name="api_key"
              type="password"
              autoComplete="off"
              value={draft.api_key}
              onChange={(e) => setDraft((d) => ({ ...d, api_key: e.target.value }))}
              placeholder={t("keep_existing_placeholder")}
              className={inputClsPlaceholder}
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label htmlFor={`${editPrefix}-baseurl`} className="mb-1 block text-xs text-gray-500">{t("base_url_optional")}</label>
              <input
                id={`${editPrefix}-baseurl`}
                name="base_url"
                type="url"
                value={draft.base_url}
                onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
                placeholder={t("default_url_placeholder")}
                className={inputClsPlaceholder}
              />
            </div>
          )}
          <div className="flex gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => void handleSaveEdit()}
              disabled={saving}
              className={primaryBtnCls}
            >
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
              {t("common:save")}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className={`inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 ${focusRing}`}
            >
              <X className="h-3 w-3" /> {t("common:cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

//AddCredentialForm


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
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-950/60 p-3 space-y-2.5">
      <div>
        <label htmlFor="cred-add-name" className="mb-1 block text-xs text-gray-500">{t("credential_name")} <span className="text-rose-400">*</span></label>
        <input
          id="cred-add-name"
          name="name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("credential_name_placeholder")}
          className={inputClsPlaceholder}
          autoFocus
        />
      </div>
      {isVertex ? (
        <div>
          <label htmlFor="cred-add-file" className="mb-1 block text-xs text-gray-500">{t("credential_file")} <span className="text-rose-400">*</span></label>
          <button
            id="cred-add-file"
            type="button"
            onClick={() => fileRef.current?.click()}
            className={`inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-800 ${focusRing}`}
          >
            <Upload className="h-3 w-3" />
            {fileRef.current?.files?.[0]?.name ?? t("select_json_file")}
          </button>
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={() => setError(null)} />
        </div>
      ) : (
        <>
          <div>
            <label htmlFor="cred-add-apikey" className="mb-1 block text-xs text-gray-500">API Key <span className="text-rose-400">*</span></label>
            <input
              id="cred-add-apikey"
              name="api_key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={inputCls}
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label htmlFor="cred-add-baseurl" className="mb-1 block text-xs text-gray-500">{t("base_url_optional")}</label>
              <input
                id="cred-add-baseurl"
                name="base_url"
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={t("default_url_placeholder")}
                className={inputClsPlaceholder}
              />
            </div>
          )}
        </>
      )}
      {error && <p className="text-xs text-rose-400" aria-live="polite">{error}</p>}
      <div className="flex gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={saving || !name.trim()}
          className={primaryBtnCls}
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          {t("add")}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={`rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 ${focusRing}`}
        >
          {t("common:cancel")}
        </button>
      </div>
    </div>
  );
}

//CredentialList — main export


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

  // 用 ref 存储 onChanged 以稳定 refresh 引用，避免父组件 re-render 导致无限循环
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

  // 用户操作后：刷新列表 + 通知父组件
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
      <div className="flex items-center gap-2 py-4 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("common:loading")}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-300">{t("credential_mgmt")}</h4>
        {!showAdd && (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--neon-500)] transition-colors hover:bg-[var(--neon-500)]/10 ${focusRing}`}
          >
            <Plus className="h-3 w-3" /> {t("add_credential")}
          </button>
        )}
      </div>

      {credentials.length === 0 && !showAdd && (
        <div className="rounded-lg border border-dashed border-gray-700 px-4 py-6 text-center">
          <p className="text-sm text-gray-500">{t("no_credentials")}</p>
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className={`mt-2 inline-flex items-center gap-1 text-xs text-[var(--neon-500)] transition-colors hover:text-[var(--neon-400)] ${focusRing}`}
          >
            <Plus className="h-3 w-3" /> {t("add_first_credential")}
          </button>
        </div>
      )}

      <div className="space-y-1">
        {credentials.map((c) => (
          <CredentialRow
            key={c.id}
            cred={c}
            providerId={providerId}
            isVertex={isVertex}
            onChanged={handleChanged}
          />
        ))}
      </div>

      {showAdd && (
        <div className="mt-2">
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
