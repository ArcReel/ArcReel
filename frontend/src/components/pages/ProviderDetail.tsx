import { useState, useEffect, useCallback, useRef } from "react";
import { ChevronRight, Eye, EyeOff, Loader2, Upload, X } from "lucide-react";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import type { ProviderConfigDetail, ProviderField, ProviderTestResult } from "@/types";

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    ready: { label: "已就绪", cls: "bg-green-900/30 text-green-400 border border-green-800/50" },
    unconfigured: { label: "未配置", cls: "bg-gray-800 text-gray-400 border border-gray-700" },
    error: { label: "异常", cls: "bg-red-900/30 text-red-400 border border-red-800/50" },
  };
  const { label, cls } = map[status] ?? { label: status, cls: "bg-gray-800 text-gray-400 border border-gray-700" };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Credentials file upload field
// ---------------------------------------------------------------------------

interface CredentialsUploadProps {
  field: ProviderField;
  providerId: string;
  onUploaded: () => void;
}

function CredentialsUploadField({ field, providerId, onUploaded }: CredentialsUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{ ok: boolean; msg: string } | null>(null);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadResult(null);
    try {
      await API.uploadVertexCredentialsForProvider(providerId, file);
      setUploadResult({ ok: true, msg: `已上传: ${file.name}` });
      onUploaded();
    } catch (err) {
      setUploadResult({ ok: false, msg: String(err) });
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div>
      <label className="mb-1.5 block text-sm text-gray-400">
        {field.label}
        {field.required && <span className="ml-1 text-red-400">*</span>}
      </label>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
        >
          {uploading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Upload className="h-4 w-4" />
          )}
          {uploading ? "上传中…" : "选择 JSON 凭证文件"}
        </button>
        {field.is_set && !uploadResult && (
          <span className="text-xs text-gray-500">已设置凭证文件</span>
        )}
        {uploadResult && (
          <span className={`text-xs ${uploadResult.ok ? "text-green-400" : "text-red-400"}`}>
            {uploadResult.msg}
          </span>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={(e) => void handleFileChange(e)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field editor
// ---------------------------------------------------------------------------

interface FieldEditorProps {
  field: ProviderField;
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}

function FieldEditor({ field, draft, setDraft }: FieldEditorProps) {
  const [showSecret, setShowSecret] = useState(false);

  const currentValue = draft[field.key] ?? field.value ?? "";

  const handleChange = (value: string) => {
    setDraft((prev) => ({ ...prev, [field.key]: value }));
  };

  const handleClear = () => {
    if (!confirm("确定要清除此密钥吗？")) return;
    setDraft((prev) => ({ ...prev, [field.key]: "" }));
  };

  const fieldId = `field-${field.key}`;

  if (field.type === "secret") {
    const displayValue = field.key in draft
      ? draft[field.key]
      : ""; // don't show masked value in input — keep placeholder

    return (
      <div>
        <label htmlFor={fieldId} className="mb-1.5 block text-sm text-gray-400">
          {field.label}
          {field.required && <span className="ml-1 text-red-400">*</span>}
        </label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              id={fieldId}
              name={field.key}
              autoComplete="off"
              type={showSecret ? "text" : "password"}
              value={displayValue}
              onChange={(e) => handleChange(e.target.value)}
              placeholder={field.is_set ? field.value_masked ?? "••••••••••" : (field.placeholder ?? "输入密钥")}
              className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 pr-9 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <button
              type="button"
              onClick={() => setShowSecret((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              aria-label={showSecret ? "隐藏" : "显示"}
            >
              {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {field.is_set && (
            <button
              type="button"
              onClick={handleClear}
              title="清除密钥"
              className="flex items-center gap-1 rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-400 hover:border-gray-600 hover:text-gray-200"
            >
              <X className="h-3 w-3" />
              清除
            </button>
          )}
        </div>
        {field.is_set && !(field.key in draft) && (
          <p className="mt-1 text-xs text-gray-600">已设置（留空则保留现有值）</p>
        )}
      </div>
    );
  }

  if (field.type === "number") {
    return (
      <div>
        <label htmlFor={fieldId} className="mb-1.5 block text-sm text-gray-400">
          {field.label}
          {field.required && <span className="ml-1 text-red-400">*</span>}
        </label>
        <input
          id={fieldId}
          name={field.key}
          autoComplete="off"
          type="number"
          value={currentValue}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={field.placeholder ?? ""}
          className="w-32 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>
    );
  }

  // text / url / file (file handled as text input for now)
  return (
    <div>
      <label htmlFor={fieldId} className="mb-1.5 block text-sm text-gray-400">
        {field.label}
        {field.required && <span className="ml-1 text-red-400">*</span>}
      </label>
      <input
        id={fieldId}
        name={field.key}
        autoComplete="off"
        type={field.type === "url" ? "url" : "text"}
        value={currentValue}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={field.placeholder ?? ""}
        className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  providerId: string;
  onSaved?: () => void;
}

const ADVANCED_KEYS = new Set(["image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"]);

export function ProviderDetail({ providerId, onSaved }: Props) {
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    let disposed = false;
    setTestResult(null);
    setDraft({});
    setDetail(null);
    API.getProviderConfig(providerId).then((res) => {
      if (!disposed) setDetail(res);
    });
    return () => { disposed = true; };
  }, [providerId]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      const patch: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(draft)) {
        patch[key] = value || null;
      }
      await API.patchProviderConfig(providerId, patch);
      const updated = await API.getProviderConfig(providerId);
      setDetail(updated);
      setDraft({});
      onSaved?.();
    } finally {
      setSaving(false);
    }
  }, [draft, providerId, onSaved]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await API.testProviderConnection(providerId);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, available_models: [], message: String(e) });
    }
    setTesting(false);
  }, [providerId]);

  if (!detail) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载中…
      </div>
    );
  }

  const basicFields: ProviderField[] = [];
  const advancedFields: ProviderField[] = [];
  for (const f of detail.fields) {
    (ADVANCED_KEYS.has(f.key) ? advancedFields : basicFields).push(f);
  }
  const hasDraft = Object.keys(draft).length > 0;

  return (
    <div className="max-w-xl">
      {/* Header */}
      <div className="mb-6 flex items-start gap-3">
        <ProviderIcon providerId={providerId} className="mt-0.5 h-7 w-7" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-gray-100">{detail.display_name}</h3>
            <StatusBadge status={detail.status} />
          </div>
          {detail.description && (
            <p className="mt-1 text-sm text-gray-500">{detail.description}</p>
          )}
        </div>
      </div>

      {/* Capabilities */}
      {detail.media_types && detail.media_types.length > 0 && (
        <div className="mb-5 flex flex-wrap gap-1.5">
          {detail.media_types.map((t) => (
            <span key={t} className="rounded-md bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
              {t === "video" ? "视频" : t === "image" ? "图片" : t}
            </span>
          ))}
        </div>
      )}

      {/* Basic fields */}
      <div className="space-y-4">
        {basicFields.map((field) =>
          field.key === "credentials_path" ? (
            <CredentialsUploadField
              key={field.key}
              field={field}
              providerId={providerId}
              onUploaded={async () => {
                const updated = await API.getProviderConfig(providerId);
                setDetail(updated);
                onSaved?.();
              }}
            />
          ) : (
            <FieldEditor key={field.key} field={field} draft={draft} setDraft={setDraft} />
          )
        )}
      </div>

      {/* Advanced section */}
      {advancedFields.length > 0 && (
        <div className="mt-6">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
          >
            <ChevronRight
              className={`h-4 w-4 transition-transform ${showAdvanced ? "rotate-90" : ""}`}
            />
            高级配置
          </button>
          {showAdvanced && (
            <div className="mt-3 space-y-4">
              {advancedFields.map((field) => (
                <FieldEditor key={field.key} field={field} draft={draft} setDraft={setDraft} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          onClick={handleTest}
          disabled={testing}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
        >
          {testing && <Loader2 className="h-4 w-4 animate-spin" />}
          测试连接
        </button>
        {hasDraft && (
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                保存中…
              </>
            ) : (
              "保存"
            )}
          </button>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div
          className={`mt-3 rounded-lg p-3 text-sm ${
            testResult.success
              ? "bg-green-900/20 text-green-400"
              : "bg-red-900/20 text-red-400"
          }`}
        >
          {testResult.message}
          {testResult.success && testResult.available_models.length > 0 && (
            <div className="mt-1 text-xs opacity-75">
              可用模型: {testResult.available_models.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
