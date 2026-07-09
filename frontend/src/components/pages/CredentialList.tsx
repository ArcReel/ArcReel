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
import type { TFunction } from "i18next";
import { API } from "@/api";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  ICON_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import type { CredentialSecretField, ProviderCredential, ProviderTestResult } from "@/types";

// 单 secret provider 的默认凭证字段，供未显式传 secretFields 的调用方兜底（行为同旧版 api_key 表单）。
const DEFAULT_SECRET_FIELDS: CredentialSecretField[] = [{ key: "api_key", label: "API Key" }];

// 已知 secret 凭证字段 → 前端 i18n label key；未知 key 回退后端提供的 label。
const SECRET_FIELD_LABEL_KEY: Record<string, string> = {
  api_key: "api_key_label",
  access_key: "access_key_label",
  secret_key: "secret_key_label",
};

// 解析 secret 字段标签：已知 key 走前端 i18n，未知 key 回退后端提供的 label。
function secretFieldLabel(t: TFunction, field: CredentialSecretField): string {
  const lk = SECRET_FIELD_LABEL_KEY[field.key];
  return lk ? t(lk) : field.label;
}

// 逐字段读取脱敏值（与后端 *_masked 列一一对应）。
function maskedForKey(cred: ProviderCredential, key: string): string | null | undefined {
  if (key === "api_key") return cred.api_key_masked;
  if (key === "access_key") return cred.access_key_masked;
  if (key === "secret_key") return cred.secret_key_masked;
  return undefined;
}

interface RowProps {
  cred: ProviderCredential;
  providerId: string;
  isVertex: boolean;
  supportsBaseUrl: boolean;
  secretFields: CredentialSecretField[];
  onChanged: () => void;
}

const CredentialRow = memo(function CredentialRow({
  cred,
  providerId,
  isVertex,
  supportsBaseUrl,
  secretFields,
  onChanged,
}: RowProps) {
  const { t } = useTranslation("dashboard");
  const [editing, setEditing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [saving, setSaving] = useState(false);
  // secrets 留空表示保留现有值；逐字段独立编辑。
  const [draft, setDraft] = useState<{ name: string; base_url: string; secrets: Record<string, string> }>({
    name: cred.name,
    base_url: cred.base_url ?? "",
    secrets: {},
  });

  const labelFor = useCallback((field: CredentialSecretField): string => secretFieldLabel(t, field), [t]);

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
    for (const field of secretFields) {
      const val = draft.secrets[field.key]?.trim();
      if (val) data[field.key] = val;
    }
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
  }, [draft, cred, providerId, secretFields, onChanged]);

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
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            {secretFields.map((field) => {
              const masked = maskedForKey(cred, field.key);
              if (!masked) return null;
              return (
                <span key={field.key} className="font-mono text-[11px] text-text-4">
                  {secretFields.length > 1 ? `${labelFor(field)}: ${masked}` : masked}
                </span>
              );
            })}
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
                setDraft({ name: cred.name, base_url: cred.base_url ?? "", secrets: {} });
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
          {secretFields.map((field) => (
            <div key={field.key}>
              <FieldLabel htmlFor={`${editPrefix}-${field.key}`}>{labelFor(field)}</FieldLabel>
              <input
                id={`${editPrefix}-${field.key}`}
                name={field.key}
                type="password"
                autoComplete="off"
                value={draft.secrets[field.key] ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, secrets: { ...d.secrets, [field.key]: e.target.value } }))
                }
                placeholder={t("keep_existing_placeholder")}
                className={INPUT_CLS}
              />
            </div>
          ))}
          {supportsBaseUrl && (
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
              className={ACCENT_BTN_SM_CLS}
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
  supportsBaseUrl: boolean;
  secretFields: CredentialSecretField[];
  // 凭证「二选一」分组：满足任一组即视为凭证完整。单组（绝大多数 provider）等价于旧版
  // 「全部必填」；可灵等多组 provider 下没有单个字段是无条件必填的，故不渲染红色必填星标。
  secretFieldGroups: string[][];
  onCreated: () => void;
  onCancel: () => void;
}

function AddCredentialForm({
  providerId,
  isVertex,
  supportsBaseUrl,
  secretFields,
  secretFieldGroups,
  onCreated,
  onCancel,
}: AddFormProps) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [baseUrl, setBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const nameRef = useAutoFocus<HTMLInputElement>();

  const labelFor = (field: CredentialSecretField): string => secretFieldLabel(t, field);
  const fieldByKey = new Map(secretFields.map((f) => [f.key, f]));
  const labelForKey = (key: string): string => labelFor(fieldByKey.get(key) ?? { key, label: key });
  // 兜底：调用方未传分组时退化为单一必填组（= 全部 secret_fields），与旧版语义一致。
  const groups = secretFieldGroups.length > 0 ? secretFieldGroups : [secretFields.map((f) => f.key)];
  // 仅单一必填组时，组内每个字段才是无条件必填（旧版行为）；多组二选一时不标红星，
  // 靠下方 orHint 提示组合关系，避免误导用户以为要填满所有字段。
  const fieldsUnconditionallyRequired = groups.length <= 1;
  const orHint = groups.length > 1 ? groups.map((g) => g.map(labelForKey).join(" + ")).join(` ${t("or_label")} `) : null;

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
        // 至少一组（组内字段全填）即视为凭证完整；单组场景等价于旧版「全部必填」。
        const groupSatisfied = (group: string[]) => group.every((k) => (secrets[k] ?? "").trim());
        if (!groups.some(groupSatisfied)) {
          setError(groups.length > 1 ? t("enter_credentials_required_any_group") : t("enter_credentials_required"));
          setSaving(false);
          return;
        }
        const payload: { name: string; [key: string]: string | undefined } = {
          name: name.trim(),
          base_url: baseUrl || undefined,
        };
        for (const field of secretFields) payload[field.key] = secrets[field.key]?.trim();
        await API.createCredential(providerId, payload);
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
            {selectedFileName ?? t("select_json_file")}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            aria-label={t("import_credential_file_aria")}
            className="hidden"
            onChange={(e) => {
              setError(null);
              setSelectedFileName(e.currentTarget.files?.[0]?.name ?? null);
            }}
          />
        </div>
      ) : (
        <>
          {orHint && <p className="text-[11px] text-text-4">{orHint}</p>}
          {secretFields.map((field) => (
            <div key={field.key}>
              <FieldLabel htmlFor={`cred-add-${field.key}`} required={fieldsUnconditionallyRequired}>
                {labelFor(field)}
              </FieldLabel>
              <input
                id={`cred-add-${field.key}`}
                name={field.key}
                type="password"
                autoComplete="off"
                value={secrets[field.key] ?? ""}
                onChange={(e) => setSecrets((s) => ({ ...s, [field.key]: e.target.value }))}
                className={INPUT_CLS}
              />
            </div>
          ))}
          {supportsBaseUrl && (
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
          className={ACCENT_BTN_SM_CLS}
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
  supportsBaseUrl: boolean;
  secretFields?: CredentialSecretField[];
  // 凭证「二选一」分组，见 AddFormProps 注释；未传时按单组全字段回退（旧版行为）。
  secretFieldGroups?: string[][];
  onChanged?: () => void;
}

export function CredentialList({ providerId, supportsBaseUrl, secretFields, secretFieldGroups, onChanged }: Props) {
  const fields = secretFields ?? DEFAULT_SECRET_FIELDS;
  const fieldGroups = secretFieldGroups ?? [fields.map((f) => f.key)];
  const { t } = useTranslation("dashboard");
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const isVertex = providerId === "gemini-vertex";

  const onChangedRef = useRef(onChanged);
  // 同步最新 onChanged 回调到 ref，供异步刷新后调用
  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

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
    // providerId 变化时重置加载态并重新拉取，属于动作驱动的状态重置
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
        {/* 子组件 onChanged 通过 voidPromise 包装 ref 持有的最新回调 */}
        {/* eslint-disable-next-line react-hooks/refs */}
        {credentials.map((c) => (
          <CredentialRow
            key={c.id}
            cred={c}
            providerId={providerId}
            isVertex={isVertex}
            supportsBaseUrl={supportsBaseUrl}
            secretFields={fields}
            onChanged={voidPromise(handleChanged)}
          />
        ))}
      </div>

      {showAdd && (
        <div className="mt-3">
          <AddCredentialForm
            providerId={providerId}
            isVertex={isVertex}
            supportsBaseUrl={supportsBaseUrl}
            secretFields={fields}
            secretFieldGroups={fieldGroups}
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
