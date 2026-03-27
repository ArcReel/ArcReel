import { useState, useEffect, useCallback, useRef } from "react";
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
import { API } from "@/api";
import type { ProviderCredential, ProviderTestResult } from "@/types";

// ---------------------------------------------------------------------------
// CredentialRow
// ---------------------------------------------------------------------------

interface RowProps {
  cred: ProviderCredential;
  providerId: string;
  isVertex: boolean;
  onChanged: () => void;
}

function CredentialRow({ cred, providerId, isVertex, onChanged }: RowProps) {
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
    if (Object.keys(data).length > 0) {
      setSaving(true);
      try {
        await API.updateCredential(providerId, cred.id, data);
      } finally {
        setSaving(false);
      }
    }
    setEditing(false);
    onChanged();
  }, [draft, cred, providerId, onChanged]);

  return (
    <div
      className={`rounded-lg border-l-2 px-3 py-2.5 transition-colors ${
        cred.is_active
          ? "border-l-[var(--neon-500)] bg-gray-900/30"
          : "border-l-transparent hover:bg-gray-800/20"
      }`}
    >
      <div className="flex items-center gap-3">
        {/* Activate dot */}
        <button
          type="button"
          onClick={cred.is_active ? undefined : handleActivate}
          disabled={cred.is_active}
          className={`h-2.5 w-2.5 flex-shrink-0 rounded-full transition-colors ${
            cred.is_active
              ? "bg-[var(--neon-500)] shadow-[0_0_6px_var(--neon-500)]"
              : "border border-gray-600 hover:border-gray-400 cursor-pointer"
          }`}
          title={cred.is_active ? "当前使用中" : "点击激活"}
        />

        {/* Info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-200">{cred.name}</span>
            {cred.is_active && (
              <span className="rounded bg-[var(--neon-500)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--neon-500)]">
                使用中
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

        {/* Actions */}
        <div className="flex flex-shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing}
            className="rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
            title="测试连接"
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
              className="rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
              title="编辑"
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
          )}
          {!confirmDelete ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-800 hover:text-rose-400"
              title="删除"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="rounded px-2 py-1 text-xs text-rose-400 transition-colors hover:bg-rose-900/20"
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "确认"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="rounded px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
              >
                取消
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <div
          className={`mt-2 ml-5.5 rounded-md px-3 py-2 text-xs ${
            testResult.success
              ? "bg-green-900/20 text-green-400"
              : "bg-rose-900/15 text-rose-400"
          }`}
        >
          {testResult.message}
          {testResult.success && testResult.available_models.length > 0 && (
            <div className="mt-1 opacity-70">
              可用模型: {testResult.available_models.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Inline edit */}
      {editing && (
        <div className="mt-2.5 ml-5.5 space-y-2.5 rounded-lg border border-gray-800 bg-gray-950/60 p-3">
          <div>
            <label className="mb-1 block text-xs text-gray-500">名称</label>
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-500">API Key（留空保留现有值）</label>
            <input
              type="password"
              value={draft.api_key}
              onChange={(e) => setDraft((d) => ({ ...d, api_key: e.target.value }))}
              placeholder="••••••••"
              className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label className="mb-1 block text-xs text-gray-500">Base URL（可选）</label>
              <input
                type="url"
                value={draft.base_url}
                onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
                placeholder="默认官方地址"
                className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
              />
            </div>
          )}
          <div className="flex gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => void handleSaveEdit()}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
              保存
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
            >
              <X className="h-3 w-3" /> 取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddCredentialForm
// ---------------------------------------------------------------------------

interface AddFormProps {
  providerId: string;
  isVertex: boolean;
  onCreated: () => void;
  onCancel: () => void;
}

function AddCredentialForm({ providerId, isVertex, onCreated, onCancel }: AddFormProps) {
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
          setError("请选择凭证文件");
          setSaving(false);
          return;
        }
        await API.uploadVertexCredential(name, file);
      } else {
        if (!apiKey.trim()) {
          setError("请输入 API Key");
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
        <label className="mb-1 block text-xs text-gray-500">名称 <span className="text-rose-400">*</span></label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：个人账号"
          className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
          autoFocus
        />
      </div>
      {isVertex ? (
        <div>
          <label className="mb-1 block text-xs text-gray-500">凭证文件 <span className="text-rose-400">*</span></label>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-800"
          >
            <Upload className="h-3 w-3" />
            {fileRef.current?.files?.[0]?.name ?? "选择 JSON 文件"}
          </button>
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={() => setError(null)} />
        </div>
      ) : (
        <>
          <div>
            <label className="mb-1 block text-xs text-gray-500">API Key <span className="text-rose-400">*</span></label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label className="mb-1 block text-xs text-gray-500">Base URL（可选）</label>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="默认官方地址"
                className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-1.5 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus:ring-1 focus:ring-indigo-500/60"
              />
            </div>
          )}
        </>
      )}
      {error && <p className="text-xs text-rose-400">{error}</p>}
      <div className="flex gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={saving || !name.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          添加
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
        >
          取消
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CredentialList — main export
// ---------------------------------------------------------------------------

interface Props {
  providerId: string;
  onChanged?: () => void;
}

export function CredentialList({ providerId, onChanged }: Props) {
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const isVertex = providerId === "gemini-vertex";

  const refresh = useCallback(async () => {
    try {
      const { credentials: creds } = await API.listCredentials(providerId);
      setCredentials(creds);
    } finally {
      setLoading(false);
    }
    onChanged?.();
  }, [providerId, onChanged]);

  useEffect(() => {
    setLoading(true);
    setShowAdd(false);
    void refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-300">密钥管理</h4>
        {!showAdd && (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--neon-500)] transition-colors hover:bg-[var(--neon-500)]/10"
          >
            <Plus className="h-3 w-3" /> 添加密钥
          </button>
        )}
      </div>

      {credentials.length === 0 && !showAdd && (
        <div className="rounded-lg border border-dashed border-gray-700 px-4 py-6 text-center">
          <p className="text-sm text-gray-500">暂无密钥</p>
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--neon-500)] transition-colors hover:text-[var(--neon-400)]"
          >
            <Plus className="h-3 w-3" /> 添加第一个密钥
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
            onChanged={() => void refresh()}
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
              void refresh();
            }}
            onCancel={() => setShowAdd(false)}
          />
        </div>
      )}
    </div>
  );
}
