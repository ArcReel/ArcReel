import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, EyeOff, Loader2, ShieldCheck, Upload, X } from "lucide-react";
import GeminiColor from "@lobehub/icons/es/Gemini/components/Color";
import VertexAIColor from "@lobehub/icons/es/VertexAI/components/Color";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type {
  GetSystemConfigResponse,
  SystemBackend,
  SystemConfigPatch,
  SystemConnectionTestResponse,
} from "@/types";
import { TabSaveFooter } from "./TabSaveFooter";
import { mergeServerDraftPreservingDirty } from "./system-config-draft-utils";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface MediaDraft {
  geminiKey: string;           // new key input (empty = don't change)
  geminiBaseUrl: string;       // in-place editing; empty = clear
  vertexGcsBucket: string;     // in-place editing; empty = clear
  imageBackend: SystemBackend;
  videoBackend: SystemBackend;
  imageModel: string;
  videoModel: string;
  videoGenerateAudio: boolean;
}

function buildDraft(data: GetSystemConfigResponse): MediaDraft {
  const cfg = data.config;
  return {
    geminiKey: "",
    geminiBaseUrl: cfg.gemini_base_url.value ?? "",
    vertexGcsBucket: cfg.vertex_gcs_bucket.value ?? "",
    imageBackend: cfg.image_backend,
    videoBackend: cfg.video_backend,
    imageModel: cfg.image_model,
    videoModel: cfg.video_model,
    videoGenerateAudio: cfg.video_generate_audio,
  };
}

function deepEqual(a: MediaDraft, b: MediaDraft): boolean {
  return (
    a.geminiKey === b.geminiKey &&
    a.geminiBaseUrl === b.geminiBaseUrl &&
    a.vertexGcsBucket === b.vertexGcsBucket &&
    a.imageBackend === b.imageBackend &&
    a.videoBackend === b.videoBackend &&
    a.imageModel === b.imageModel &&
    a.videoModel === b.videoModel &&
    a.videoGenerateAudio === b.videoGenerateAudio
  );
}

function buildPatch(draft: MediaDraft, saved: MediaDraft): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (draft.geminiKey.trim()) patch.gemini_api_key = draft.geminiKey.trim();
  if (draft.geminiBaseUrl !== saved.geminiBaseUrl)
    patch.gemini_base_url = draft.geminiBaseUrl || "";
  if (draft.vertexGcsBucket !== saved.vertexGcsBucket)
    patch.vertex_gcs_bucket = draft.vertexGcsBucket || "";
  if (draft.imageBackend !== saved.imageBackend) patch.image_backend = draft.imageBackend;
  if (draft.videoBackend !== saved.videoBackend) patch.video_backend = draft.videoBackend;
  if (draft.imageModel !== saved.imageModel) patch.image_model = draft.imageModel;
  if (draft.videoModel !== saved.videoModel) patch.video_model = draft.videoModel;
  if (draft.videoGenerateAudio !== saved.videoGenerateAudio)
    patch.video_generate_audio = draft.videoGenerateAudio;
  return patch;
}

// ---------------------------------------------------------------------------
// Shared style constants
// ---------------------------------------------------------------------------

const cardClassName = "rounded-xl border border-gray-800 bg-gray-950/40 p-4";
const inputClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60";
const selectClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60";
const secondaryButtonClassName =
  "inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80 disabled:cursor-not-allowed disabled:opacity-60";
const vendorIconFrameClassName =
  "rounded-2xl border border-gray-800 bg-gray-900 px-3 py-3 shadow-inner shadow-white/5";
const infoStripClassName =
  "mt-3 flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2";
const successNoteClassName =
  "mt-3 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2 text-xs text-gray-300";
const errorNoteClassName =
  "mt-3 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-100";
const inlineClearClassName =
  "ml-1.5 inline-flex items-center rounded p-0.5 text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50";

type ProviderTestState =
  | { status: "idle" }
  | { status: "success"; result: SystemConnectionTestResponse }
  | { status: "error"; message: string };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface MediaConfigTabProps {
  data: GetSystemConfigResponse;
  onSaved: (updated: GetSystemConfigResponse) => void;
  onDirtyChange: (dirty: boolean) => void;
  visible: boolean;
}

export function MediaConfigTab({ data, onSaved, onDirtyChange, visible }: MediaConfigTabProps) {
  const initialDraft = buildDraft(data);
  const [draft, setDraft] = useState<MediaDraft>(initialDraft);
  const savedRef = useRef<MediaDraft>(initialDraft);
  const [saving, setSaving] = useState(false);
  const [clearingField, setClearingField] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [testingProvider, setTestingProvider] = useState<SystemBackend | null>(null);
  const [aistudioTestState, setAistudioTestState] = useState<ProviderTestState>({
    status: "idle",
  });
  const [vertexTestState, setVertexTestState] = useState<ProviderTestState>({ status: "idle" });
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const isDirty = !deepEqual(draft, savedRef.current);

  const prevDirtyRef = useRef(isDirty);
  useEffect(() => {
    if (prevDirtyRef.current === isDirty) return;
    prevDirtyRef.current = isDirty;
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  const updateDraft = useCallback(
    <K extends keyof MediaDraft>(key: K, value: MediaDraft[K]) => {
      setDraft((prev) => ({ ...prev, [key]: value }));
      setSaveError(null);
    },
    [],
  );

  const handleSave = useCallback(async () => {
    const patch = buildPatch(draft, savedRef.current);
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await API.updateSystemConfig(patch);
      const newDraft = buildDraft(res);
      savedRef.current = newDraft;
      setDraft(newDraft);
      onSaved(res);
      if ("gemini_api_key" in patch) setAistudioTestState({ status: "idle" });
      useConfigStatusStore.getState().refresh();
      useAppStore.getState().pushToast("AI 媒体配置已保存", "success");
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [draft, onSaved]);

  const handleReset = useCallback(() => {
    setDraft(savedRef.current);
    setSaveError(null);
  }, []);

  // Generic: immediately PATCH a single field to empty (""), used by inline clear buttons
  const handleClearField = useCallback(
    async (fieldId: string, patch: SystemConfigPatch, label: string) => {
      setClearingField(fieldId);
      try {
        const res = await API.updateSystemConfig(patch);
        const previousSavedDraft = savedRef.current;
        const nextSavedDraft = buildDraft(res);
        savedRef.current = nextSavedDraft;
        setDraft((prev) =>
          mergeServerDraftPreservingDirty(prev, previousSavedDraft, nextSavedDraft),
        );
        onSaved(res);
        if ("gemini_api_key" in patch) setAistudioTestState({ status: "idle" });
        useConfigStatusStore.getState().refresh();
        useAppStore.getState().pushToast(`${label} 已清除`, "success");
      } catch (err) {
        useAppStore.getState().pushToast(`清除失败: ${(err as Error).message}`, "error");
      } finally {
        setClearingField(null);
      }
    },
    [onSaved],
  );

  const handleUploadVertex = useCallback(async (file: File) => {
    setUploading(true);
    try {
      const res = await API.uploadVertexCredentials(file);
      const previousSavedDraft = savedRef.current;
      const nextSavedDraft = buildDraft(res);
      savedRef.current = nextSavedDraft;
      setDraft((prev) =>
        mergeServerDraftPreservingDirty(prev, previousSavedDraft, nextSavedDraft),
      );
      onSaved(res);
      setVertexTestState({ status: "idle" });
      useConfigStatusStore.getState().refresh();
      useAppStore.getState().pushToast("Vertex 凭证已上传", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`上传失败: ${(err as Error).message}`, "error");
    } finally {
      setUploading(false);
    }
  }, [onSaved]);

  const handleTestConnection = useCallback(
    async (provider: SystemBackend) => {
      setTestingProvider(provider);
      if (provider === "aistudio") {
        setAistudioTestState({ status: "idle" });
      } else {
        setVertexTestState({ status: "idle" });
      }
      try {
        const res = await API.testSystemConnection({
          provider,
          image_backend: draft.imageBackend,
          video_backend: draft.videoBackend,
          image_model: draft.imageModel,
          video_model: draft.videoModel,
          gemini_api_key: provider === "aistudio" ? draft.geminiKey.trim() || null : null,
        });
        if (provider === "aistudio") {
          setAistudioTestState({ status: "success", result: res });
        } else {
          setVertexTestState({ status: "success", result: res });
        }
        useAppStore.getState().pushToast(res.message, "success");
      } catch (err) {
        const message = (err as Error).message;
        if (provider === "aistudio") {
          setAistudioTestState({ status: "error", message });
        } else {
          setVertexTestState({ status: "error", message });
        }
        useAppStore.getState().pushToast(message, "error");
      } finally {
        setTestingProvider(null);
      }
    },
    [draft],
  );

  const cfg = data.config;
  const imageModels = data.options.image_models;
  const videoModels = data.options.video_models;
  const vertexStatus = cfg.vertex_credentials;
  const geminiKeyAvailable = Boolean(draft.geminiKey.trim() || cfg.gemini_api_key.is_set);
  const audioEditable = draft.videoBackend === "vertex";
  const audioEffective = audioEditable ? draft.videoGenerateAudio : true;
  const isBusy = saving || clearingField !== null;

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-5 px-6 pb-0 pt-6">
        {/* Gemini API Key */}
        <div className={cardClassName}>
          <div className="flex items-start gap-3">
            <div className={vendorIconFrameClassName}>
              <GeminiColor size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-gray-100">Gemini API Key</div>
              <div className="mt-1 text-xs text-gray-400">
                用于生成分镜图片和视频片段，选择 AI Studio 后端时需要此密钥。
              </div>
              {/* 当前值行 — 内联清除按钮仅在 override 时显示 */}
              <div className="mt-1 flex items-center text-xs text-gray-500">
                <span className="truncate">
                  当前：{cfg.gemini_api_key.masked ?? "未设置"}
                  {cfg.gemini_api_key.source === "env" && (
                    <> · <span className="text-gray-400">.env</span></>
                  )}
                </span>
                {cfg.gemini_api_key.source === "override" && cfg.gemini_api_key.is_set && (
                  <button
                    type="button"
                    onClick={() =>
                      void handleClearField(
                        "gemini_api_key",
                        { gemini_api_key: "" },
                        "Gemini API Key",
                      )
                    }
                    disabled={isBusy}
                    className={inlineClearClassName}
                    aria-label="清除已保存的 Gemini API Key"
                  >
                    {clearingField === "gemini_api_key" ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <X className="h-3 w-3" />
                    )}
                  </button>
                )}
              </div>
            </div>
          </div>
          <div className="relative mt-3">
            <input
              type={showGeminiKey ? "text" : "password"}
              value={draft.geminiKey}
              onChange={(e) => {
                updateDraft("geminiKey", e.target.value);
                setAistudioTestState({ status: "idle" });
              }}
              placeholder="AIza…"
              className={`${inputClassName} pr-10`}
              autoComplete="off"
              spellCheck={false}
              name="gemini_api_key"
              aria-label="Gemini API Key"
              disabled={saving}
            />
            {draft.geminiKey && (
              <button
                type="button"
                onClick={() => updateDraft("geminiKey", "")}
                className="absolute right-8 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                aria-label="清除输入"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowGeminiKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
              aria-label={showGeminiKey ? "隐藏密钥" : "显示密钥"}
            >
              {showGeminiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <div className={infoStripClassName}>
            <div className="text-xs text-gray-300">
              验证当前模型可用性。填入新 Key 时优先验证新 Key，不影响已保存配置。
            </div>
            <button
              type="button"
              onClick={() => void handleTestConnection("aistudio")}
              disabled={saving || uploading || testingProvider !== null || !geminiKeyAvailable}
              className={secondaryButtonClassName}
            >
              {testingProvider === "aistudio" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ShieldCheck className="h-4 w-4" />
              )}
              {testingProvider === "aistudio" ? "测试中…" : "测试连接"}
            </button>
          </div>
          {aistudioTestState.status === "success" && (
            <div className={successNoteClassName} aria-live="polite">
              {aistudioTestState.result.message}
            </div>
          )}
          {aistudioTestState.status === "error" && (
            <div className={errorNoteClassName} aria-live="polite">
              {aistudioTestState.message}
            </div>
          )}

          {/* Gemini Base URL */}
          <div className="mt-4 border-t border-gray-800 pt-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-gray-100">Base URL</div>
              {cfg.gemini_base_url.source === "override" && cfg.gemini_base_url.value && (
                <button
                  type="button"
                  onClick={() =>
                    void handleClearField(
                      "gemini_base_url",
                      { gemini_base_url: "" },
                      "Gemini Base URL",
                    )
                  }
                  disabled={isBusy}
                  className="inline-flex items-center gap-1 text-xs text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label="清除已保存的 Gemini Base URL"
                >
                  {clearingField === "gemini_base_url" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                  清除已保存
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-gray-400">
              可选。留空使用官方默认地址，使用代理网关时填写自定义地址。
            </div>
            <div className="relative mt-3">
              <input
                value={draft.geminiBaseUrl}
                onChange={(e) => updateDraft("geminiBaseUrl", e.target.value)}
                placeholder="https://gemini-proxy.example.com"
                className={`${inputClassName}${draft.geminiBaseUrl ? " pr-8" : ""}`}
                autoComplete="off"
                spellCheck={false}
                name="gemini_base_url"
                aria-label="Gemini Base URL"
                disabled={saving}
              />
              {draft.geminiBaseUrl && (
                <button
                  type="button"
                  onClick={() => updateDraft("geminiBaseUrl", "")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                  aria-label="清除 Gemini Base URL 输入"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Vertex AI */}
        <div className={cardClassName}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className={vendorIconFrameClassName}>
                <VertexAIColor size={20} />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-100">Vertex AI 凭证</div>
                <div className="mt-1 text-xs text-gray-400">
                  Google Cloud 企业级后端，与 AI Studio 使用相同模型，提供更高配额和 SLA 保障。
                </div>
                <div className="mt-1 truncate text-xs text-gray-500">
                  {vertexStatus.is_set ? (
                    <>
                      已上传：<span className="text-gray-200">{vertexStatus.filename}</span>
                      {vertexStatus.project_id ? (
                        <>
                          {" "}· 项目：
                          <span className="text-gray-200">{vertexStatus.project_id}</span>
                        </>
                      ) : null}
                    </>
                  ) : (
                    <>未上传 · 切换到 Vertex AI 前请先上传 JSON 凭证</>
                  )}
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => uploadInputRef.current?.click()}
              disabled={uploading || testingProvider !== null}
              className={secondaryButtonClassName}
            >
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              上传 JSON
            </button>
            <input
              ref={uploadInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              aria-label="上传 Vertex AI JSON 凭证文件"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                void handleUploadVertex(file);
              }}
            />
          </div>
          <div className={infoStripClassName}>
            <div className="text-xs text-gray-300">验证当前模型可用性。需先上传凭证文件。</div>
            <button
              type="button"
              onClick={() => void handleTestConnection("vertex")}
              disabled={uploading || saving || testingProvider !== null || !vertexStatus.is_set}
              className={secondaryButtonClassName}
            >
              {testingProvider === "vertex" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ShieldCheck className="h-4 w-4" />
              )}
              {testingProvider === "vertex" ? "测试中…" : "测试连接"}
            </button>
          </div>
          {vertexTestState.status === "success" && (
            <div className={successNoteClassName} aria-live="polite">
              {vertexTestState.result.message}
              {vertexTestState.result.project_id && (
                <span className="text-gray-500"> · 项目 {vertexTestState.result.project_id}</span>
              )}
            </div>
          )}
          {vertexTestState.status === "error" && (
            <div className={errorNoteClassName} aria-live="polite">
              {vertexTestState.message}
            </div>
          )}

          {/* GCS Bucket */}
          <div className="mt-4 border-t border-gray-800 pt-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-gray-100">GCS Bucket</div>
              {cfg.vertex_gcs_bucket.source === "override" && cfg.vertex_gcs_bucket.value && (
                <button
                  type="button"
                  onClick={() =>
                    void handleClearField(
                      "vertex_gcs_bucket",
                      { vertex_gcs_bucket: "" },
                      "GCS Bucket",
                    )
                  }
                  disabled={isBusy}
                  className="inline-flex items-center gap-1 text-xs text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label="清除已保存的 GCS Bucket"
                >
                  {clearingField === "vertex_gcs_bucket" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                  清除已保存
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-gray-500">
              Vertex AI 延长视频时需要 GCS 存储。格式：bucket-name（不含 gs:// 前缀）
            </div>
            <div className="relative mt-2">
              <input
                type="text"
                placeholder="your-gcs-bucket-name"
                value={draft.vertexGcsBucket}
                onChange={(e) => updateDraft("vertexGcsBucket", e.target.value)}
                className={`${inputClassName}${draft.vertexGcsBucket ? " pr-8" : ""}`}
                name="vertex_gcs_bucket"
                disabled={saving}
              />
              {draft.vertexGcsBucket && (
                <button
                  type="button"
                  onClick={() => updateDraft("vertexGcsBucket", "")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                  aria-label="清除 GCS Bucket 输入"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Image backend & model */}
        <div className="grid gap-5 md:grid-cols-2">
          <div className={cardClassName}>
            <div className="text-sm font-medium text-gray-100">图片后端</div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {(["aistudio", "vertex"] as const).map((b) => (
                <label
                  key={b}
                  className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                    draft.imageBackend === b
                      ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                      : "border-gray-800 bg-gray-900/80 text-gray-300 hover:border-gray-700 hover:bg-gray-900"
                  }`}
                >
                  <span>{b === "aistudio" ? "AI Studio" : "Vertex AI"}</span>
                  <input
                    type="radio"
                    name="image_backend"
                    checked={draft.imageBackend === b}
                    onChange={() => updateDraft("imageBackend", b)}
                    className="sr-only"
                    disabled={saving}
                  />
                </label>
              ))}
            </div>
            <label
              className="mt-4 text-sm font-medium text-gray-100"
              htmlFor="media_image_model"
            >
              图片模型
            </label>
            <select
              id="media_image_model"
              value={draft.imageModel}
              onChange={(e) => updateDraft("imageModel", e.target.value)}
              className={`mt-2 ${selectClassName}`}
              name="image_model"
              disabled={saving}
            >
              {imageModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>

          <div className={cardClassName}>
            <div className="text-sm font-medium text-gray-100">视频后端</div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {(["aistudio", "vertex"] as const).map((b) => (
                <label
                  key={b}
                  className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                    draft.videoBackend === b
                      ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                      : "border-gray-800 bg-gray-900/80 text-gray-300 hover:border-gray-700 hover:bg-gray-900"
                  }`}
                >
                  <span>{b === "aistudio" ? "AI Studio" : "Vertex AI"}</span>
                  <input
                    type="radio"
                    name="video_backend"
                    checked={draft.videoBackend === b}
                    onChange={() => updateDraft("videoBackend", b)}
                    className="sr-only"
                    disabled={saving}
                  />
                </label>
              ))}
            </div>
            <label
              className="mt-4 text-sm font-medium text-gray-100"
              htmlFor="media_video_model"
            >
              视频模型
            </label>
            <select
              id="media_video_model"
              value={draft.videoModel}
              onChange={(e) => updateDraft("videoModel", e.target.value)}
              className={`mt-2 ${selectClassName}`}
              name="video_model"
              disabled={saving}
            >
              {videoModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <div className="mt-4 flex items-start justify-between gap-3 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={draft.videoGenerateAudio}
                  onChange={(e) => updateDraft("videoGenerateAudio", e.target.checked)}
                  disabled={!audioEditable || saving}
                  className="mt-1 h-4 w-4 rounded border-gray-700 bg-gray-900"
                />
                <span className="text-sm text-gray-200">
                  生成音频
                  <span className="ml-2 text-xs text-gray-500">
                    {audioEditable ? "（Vertex 可选关闭）" : "（AI Studio 始终开启）"}
                  </span>
                </span>
              </label>
              <span className="text-xs text-gray-500">
                当前状态：{audioEffective ? "开启" : "关闭"}
              </span>
            </div>
          </div>
        </div>
      </div>

      <TabSaveFooter
        isDirty={isDirty}
        saving={saving}
        disabled={clearingField !== null || uploading}
        error={saveError}
        onSave={() => void handleSave()}
        onReset={handleReset}
      />
    </div>
  );
}
