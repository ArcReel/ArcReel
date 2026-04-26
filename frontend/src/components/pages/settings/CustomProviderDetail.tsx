import { useState, useEffect, useCallback } from "react";
import { Loader2, Pencil, Trash2, CheckCircle2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { CustomProviderInfo } from "@/types";
import { ENDPOINT_TO_MEDIA_TYPE } from "@/types";
import { CustomProviderForm } from "./CustomProviderForm";

// ---------------------------------------------------------------------------
// Media type label
// ---------------------------------------------------------------------------

const MEDIA_LABELS: Record<string, string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface CustomProviderDetailProps {
  providerId: number;
  onDeleted: () => void;
  onSaved: () => void;
}

export function CustomProviderDetail({ providerId, onDeleted, onSaved }: CustomProviderDetailProps) {
  const { t } = useTranslation("dashboard");
  const [provider, setProvider] = useState<CustomProviderInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const showError = useCallback((msg: string) => useAppStore.getState().pushToast(msg, "error"), []);

  const fetchProvider = useCallback(async () => {
    setLoading(true);
    try {
      const data = await API.getCustomProvider(providerId);
      setProvider(data);
    } finally {
      setLoading(false);
    }
  }, [providerId]);

  useEffect(() => {
    setEditing(false);
    setConfirmDelete(false);
    setTestResult(null);
    void fetchProvider();
  }, [fetchProvider]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await API.deleteCustomProvider(providerId);
      onDeleted();
    } catch (e) {
      showError(t("delete_failed", { message: errMsg(e) }));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [providerId, onDeleted, showError, t]);

  const handleTest = useCallback(async () => {
    if (!provider) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await API.testCustomConnectionById(provider.id);
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: errMsg(e, t("connection_test_failed")) });
    } finally {
      setTesting(false);
    }
  }, [provider, t]);

  const handleFormSaved = useCallback(() => {
    setEditing(false);
    void fetchProvider();
    onSaved();
  }, [fetchProvider, onSaved]);

  if (loading || !provider) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("common:loading")}
      </div>
    );
  }

  // --- Edit mode ---
  if (editing) {
    return (
      <CustomProviderForm
        existing={provider}
        onSaved={handleFormSaved}
        onCancel={() => setEditing(false)}
      />
    );
  }

  // --- Read mode ---
  const ready = provider.base_url && provider.api_key_masked;

  return (
    <div>
      {/* Detail content (scroll handled by ancestor <main>) */}
      <div className="p-6 pb-24">
      <div className="max-w-xl">
      {/* Header */}
      <div className="mb-6 flex items-start gap-3">
        <span className="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded bg-gray-700 text-sm font-bold uppercase text-gray-300">
          {provider.display_name?.[0] ?? "?"}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-gray-100">{provider.display_name}</h3>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                ready
                  ? "border border-green-800/50 bg-green-900/30 text-green-400"
                  : "border border-gray-700 bg-gray-800 text-gray-400"
              }`}
            >
              {ready ? t("status_connected") : t("status_unconfigured")}
            </span>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            {provider.discovery_format === "openai" ? "OpenAI" : "Google"} &middot; {provider.base_url}
          </p>
        </div>
      </div>

      {/* Info card */}
      <div className="mb-5 rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">{t("discovery_format_label")}</span>
            <span className="text-gray-300">
              {provider.discovery_format === "openai" ? "OpenAI" : "Google"}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Base URL</span>
            <span className="truncate pl-4 text-gray-300">{provider.base_url}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">API Key</span>
            <span className="text-gray-300">{provider.api_key_masked || t("api_key_not_set")}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">{t("created_at")}</span>
            <span className="text-gray-300">
              {new Date(provider.created_at).toLocaleDateString("zh-CN")}
            </span>
          </div>
        </div>
      </div>

      {/* Model list */}
      {provider.models.length > 0 && (
        <div className="mb-5">
          <div className="mb-2 text-sm text-gray-400">{t("model_list")}</div>
          <div className="space-y-1.5">
            {provider.models.map((m) => (
              <div
                key={m.id}
                className={`flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-950/40 px-3 py-2 text-sm ${
                  m.is_enabled ? "text-gray-200" : "text-gray-500 opacity-60"
                }`}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{m.model_id}</span>
                <span className="rounded bg-gray-800 px-1.5 py-0.5 text-xs text-gray-400">
                  {(() => {
                    const media = ENDPOINT_TO_MEDIA_TYPE[m.endpoint];
                    return MEDIA_LABELS[media] ? t(MEDIA_LABELS[media]) : media;
                  })()}
                </span>
                {m.is_default && (
                  <span className="rounded bg-indigo-600/30 px-1.5 py-0.5 text-xs text-indigo-300">
                    {t("default_label")}
                  </span>
                )}
                {!m.is_enabled && (
                  <span className="text-xs text-gray-600">{t("model_disabled")}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Test result */}
      {testResult && (
        <div
          aria-live="polite"
          className={`mb-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
            testResult.success
              ? "border-green-800/50 bg-green-900/20 text-green-400"
              : "border-red-800/50 bg-red-900/20 text-red-400"
          }`}
        >
          {testResult.success ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          ) : (
            <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          )}
          <span>{testResult.message}</span>
        </div>
      )}

      </div>{/* end max-w-xl */}
      </div>{/* end detail content */}

      {/* Sticky actions bar — pinned to <main> viewport bottom while detail content scrolls.
          Solid background to fully occlude the scrolling list underneath. */}
      <div className="sticky bottom-0 z-10 border-t border-gray-800 bg-gray-950 px-6 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white transition-colors hover:bg-indigo-500 focus-ring"
          >
            <Pencil className="h-3.5 w-3.5" />
            {t("common:edit")}
          </button>

          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={testing}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:text-gray-100 disabled:opacity-50 focus-ring"
          >
            {testing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("testing_connection")}
              </>
            ) : (
              t("test_connection")
            )}
          </button>

          {!confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-400 transition-colors hover:border-red-800 hover:text-red-400 focus-ring"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("common:delete")}
            </button>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={deleting}
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-800 bg-red-900/30 px-3 py-1.5 text-sm text-red-400 hover:bg-red-900/50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60"
              >
                {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                {t("confirm_delete_provider")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-400 hover:border-gray-600 hover:text-gray-200 focus-ring"
              >
                {t("common:cancel")}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
