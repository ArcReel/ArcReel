
/**
 * API Keys 管理 Tab
 * 列表展示、创建（弹窗显示完整 key）、删除（确认弹窗）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import {
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { copyText } from "@/utils/clipboard";
import type { ApiKeyInfo, CreateApiKeyResponse } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null, locale: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const dateLocale = locale.startsWith("zh") ? "zh-CN" : "en-US";
  return d.toLocaleDateString(dateLocale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function isExpired(expiresAt: string | null): boolean {
  if (!expiresAt) return false;
  return new Date(expiresAt) < new Date();
}

// ---------------------------------------------------------------------------
// Create Modal
// ---------------------------------------------------------------------------

interface CreateModalProps {
  onClose: () => void;
  onCreated: (key: ApiKeyInfo) => void;
}

function CreateModal({ onClose, onCreated }: CreateModalProps) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [expiresDays, setExpiresDays] = useState<number | "">(30);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreateApiKeyResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const canCreate = useMemo(() => name.trim().length > 0, [name]);
  const nameInputRef = useAutoFocus<HTMLInputElement>();

  const handleCreate = useCallback(async () => {
    if (!canCreate || creating) return;
    setCreating(true);
    try {
      // expiresDays === "" 或 0 时发送 0（后端解释为永不过期）；
      // 正整数直接传递；undefined 让后端使用默认值（30天）。
      const days: number | undefined = expiresDays === "" ? 0 : expiresDays;
      const res = await API.createApiKey(name.trim(), days);
      setCreated(res);
      onCreated({
        id: res.id,
        name: res.name,
        key_prefix: res.key_prefix,
        created_at: res.created_at,
        expires_at: res.expires_at,
        last_used_at: null,
      });
    } catch (err) {
      useAppStore.getState().pushToast(`${t("create_failed")}${(err as Error).message}`, "error");
    } finally {
      setCreating(false);
    }
  }, [canCreate, creating, expiresDays, name, onCreated, t]);

  const handleCopy = useCallback(async () => {
    if (!created?.key) return;
    await copyText(created.key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [created?.key]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !created && canCreate) void handleCreate();
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [canCreate, created, handleCreate, onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
    >
      <div className="w-full max-w-md overflow-hidden rounded-2xl border border-gray-800 bg-gray-950 p-6 shadow-2xl">
        <div className="mb-6 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-100">
            {created ? t("key_created") : t("new_api_key")}
          </h3>
          {!creating && (
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
            >
              <X className="h-5 w-5" />
            </button>
          )}
        </div>

        {!created ? (
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-300">
                {t("name")}
              </label>
              <p className="mt-1 text-xs text-gray-500">
                {t("key_name_hint")}
              </p>
              <input
                ref={nameInputRef}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("enter_key_name")}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-100 outline-none transition-colors focus:border-indigo-500/50"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300">
                {t("expiration_days")}
              </label>
              <p className="mt-1 text-xs text-gray-500">
                {t("zero_permanent_hint")}
              </p>
              <input
                type="number"
                min={0}
                value={expiresDays}
                onChange={(e) =>
                  setExpiresDays(e.target.value === "" ? "" : Number(e.target.value))
                }
                className="mt-2 w-1/3 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-100 outline-none transition-colors focus:border-indigo-500/50"
              />
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-sm font-medium text-gray-400 hover:bg-gray-800 hover:text-gray-200"
              >
                {t("common:cancel")}
              </button>
              <button
                type="button"
                onClick={() => void handleCreate()}
                disabled={!canCreate || creating}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
              >
                {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("common:confirm")}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-xs text-amber-200/80">
              <div className="mb-2 flex items-center gap-2 font-medium text-amber-400">
                <AlertTriangle className="h-4 w-4" />
                {t("save_key_warning")}
              </div>
              {t("key_not_viewable_again")}
            </div>

            <div className="relative group">
              <input
                readOnly
                type="text"
                value={created.key}
                className="w-full rounded-lg border border-gray-800 bg-gray-900/50 px-3 py-3 pr-12 font-mono text-sm text-indigo-300 outline-none"
              />
              <button
                type="button"
                onClick={() => void handleCopy()}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-2 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
                title={t("common:copy")}
              >
                {copied ? (
                  <Check className="h-4 w-4 text-emerald-500" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg bg-gray-800 px-6 py-2 text-sm font-medium text-gray-200 transition-colors hover:bg-gray-700"
              >
                {t("common:done")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ApiKeysTab
// ---------------------------------------------------------------------------

export function ApiKeysTab() {
  const { t, i18n } = useTranslation("dashboard");
  const tRef = useRef(t);
  tRef.current = t;
  const [keys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const fetchKeys = useCallback(async () => {
    try {
      const res = await API.listApiKeys();
      setApiKeys(res);
    } catch (err) {
      useAppStore.getState().pushToast(`${tRef.current("load_failed")}${(err as Error).message}`, "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchKeys();
  }, [fetchKeys]);

  const handleDelete = useCallback(
    async (key: ApiKeyInfo) => {
      if (!confirm(tRef.current("confirm_delete_key", { name: key.name }))) {
        return;
      }
      setDeletingId(key.id);
      try {
        await API.deleteApiKey(key.id);
        setApiKeys((prev) => prev.filter((k) => k.id !== key.id));
        useAppStore.getState().pushToast(tRef.current("key_deleted_success"), "success");
      } catch (err) {
        useAppStore.getState().pushToast(`${tRef.current("delete_failed")}${(err as Error).message}`, "error");
      } finally {
        setDeletingId(null);
      }
    },
    [],
  );

  return (
    <div className="space-y-6">
      {/* Intro */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="rounded-2xl border border-gray-800 bg-gray-900 p-3 shadow-inner shadow-white/5">
            <KeyRound className="h-6 w-6 text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-100">{t("api_key_mgmt")}</h2>
            <p className="text-sm text-gray-500">
              {t("api_key_usage_desc")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 shadow-lg shadow-indigo-500/20"
        >
          <Plus className="h-4 w-4" />
          {t("create_api_key")}
        </button>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-gray-800 bg-gray-950/40">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900/50">
              <th className="px-4 py-3 font-medium text-gray-400">{t("name")}</th>
              <th className="px-4 py-3 font-medium text-gray-400">{t("key_prefix")}</th>
              <th className="px-4 py-3 font-medium text-gray-400">{t("created_at")}</th>
              <th className="px-4 py-3 font-medium text-gray-400">{t("expires_at")}</th>
              <th className="px-4 py-3 font-medium text-gray-400">{t("last_used")}</th>
              <th className="px-4 py-3 text-right font-medium text-gray-400">
                {t("actions")}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-gray-500">
                  <div className="flex items-center justify-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
                    {t("common:loading")}
                  </div>
                </td>
              </tr>
            ) : keys.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center">
                  <div className="mx-auto flex max-w-[200px] flex-col items-center gap-3">
                    <div className="rounded-full bg-gray-900/50 p-3">
                      <KeyRound className="h-6 w-6 text-gray-700" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-gray-400">{t("no_api_keys")}</p>
                      <button
                        onClick={() => setShowCreate(true)}
                        className="text-xs text-indigo-400 hover:text-indigo-300"
                      >
                        {t("create_one_now")}
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            ) : (
              keys.map((key) => {
                const expired = isExpired(key.expires_at);
                return (
                  <tr key={key.id} className="group transition-colors hover:bg-gray-900/30">
                    <td className="px-4 py-4 font-medium text-gray-200">
                      {key.name}
                    </td>
                    <td className="px-4 py-4 font-mono text-gray-500">
                      {key.key_prefix}****
                    </td>
                    <td className="px-4 py-4 text-gray-400">
                      {formatDate(key.created_at, i18n.language)}
                    </td>
                    <td className="px-4 py-4">
                      {!key.expires_at ? (
                        <span className="text-gray-500">{t("permanent")}</span>
                      ) : expired ? (
                        <span className="inline-flex rounded-full bg-rose-500/10 px-2 py-0.5 text-xs font-medium text-rose-400">
                          {t("expired")}
                        </span>
                      ) : (
                        <span className="text-gray-400">
                          {formatDate(key.expires_at, i18n.language)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4 text-gray-400">
                      {formatDate(key.last_used_at, i18n.language)}
                    </td>
                    <td className="px-4 py-4 text-right">
                      <button
                        type="button"
                        onClick={() => void handleDelete(key)}
                        disabled={deletingId === key.id}
                        className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-rose-500/10 hover:text-rose-400"
                        title={t("common:delete")}
                      >
                        {deletingId === key.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(k) => setApiKeys((prev) => [k, ...prev])}
        />
      )}
    </div>
  );
}
