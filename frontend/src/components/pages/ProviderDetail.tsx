import { useState, useEffect, useCallback } from "react";
import { ChevronRight, Eye, EyeOff, Loader2, X } from "lucide-react";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { CredentialList } from "@/components/pages/CredentialList";
import type { ProviderConfigDetail, ProviderField } from "@/types";

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_BADGE_MAP: Record<string, { label: string; cls: string }> = {
  ready: { label: "已就绪", cls: "bg-green-900/30 text-green-400 border border-green-800/50" },
  unconfigured: { label: "未配置", cls: "bg-gray-800 text-gray-400 border border-gray-700" },
  error: { label: "异常", cls: "bg-red-900/30 text-red-400 border border-red-800/50" },
};

function StatusBadge({ status }: { status: string }) {
  const { label, cls } = STATUS_BADGE_MAP[status] ?? STATUS_BADGE_MAP.unconfigured;
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {label}
    </span>
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
  const [confirmingClear, setConfirmingClear] = useState(false);

  const currentValue = draft[field.key] ?? field.value ?? "";

  const handleChange = (value: string) => {
    setDraft((prev) => ({ ...prev, [field.key]: value }));
  };

  const handleClear = () => {
    if (!confirmingClear) {
      setConfirmingClear(true);
      return;
    }
    setDraft((prev) => ({ ...prev, [field.key]: "" }));
    setConfirmingClear(false);
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
          {field.is_set && !confirmingClear && (
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
          {confirmingClear && (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={handleClear}
                className="rounded-lg border border-red-800 bg-red-900/30 px-3 py-2 text-xs text-red-400 hover:bg-red-900/50"
              >
                确认清除
              </button>
              <button
                type="button"
                onClick={() => setConfirmingClear(false)}
                className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-400 hover:border-gray-600 hover:text-gray-200"
              >
                取消
              </button>
            </div>
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

export function ProviderDetail({ providerId, onSaved }: Props) {
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    let disposed = false;
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

  if (!detail) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载中…
      </div>
    );
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

      {/* Credentials */}
      <CredentialList providerId={providerId} onChanged={onSaved} />

      {/* Shared config (all remaining fields from the API are "advanced") */}
      {detail.fields.length > 0 && (
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
              {detail.fields.map((field) => (
                <FieldEditor key={field.key} field={field} draft={draft} setDraft={setDraft} />
              ))}
              {hasDraft && (
                <div className="pt-2">
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
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
