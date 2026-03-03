import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "wouter";
import {
  ChevronLeft,
  Loader2,
  Upload,
  Save,
  KeyRound,
  Cpu,
  Gauge,
  SlidersHorizontal,
} from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { GetSystemConfigResponse, SystemBackend, SystemConfigPatch } from "@/types";

interface DraftState {
  image_backend: SystemBackend;
  video_backend: SystemBackend;
  image_model: string;
  video_model: string;
  video_generate_audio: boolean;
  gemini_image_rpm: number;
  gemini_video_rpm: number;
  gemini_request_gap: number;
  storyboard_max_workers: number;
  video_max_workers: number;
}

function buildDraft(data: GetSystemConfigResponse): DraftState {
  const cfg = data.config;
  return {
    image_backend: cfg.image_backend,
    video_backend: cfg.video_backend,
    image_model: cfg.image_model,
    video_model: cfg.video_model,
    video_generate_audio: cfg.video_generate_audio,
    gemini_image_rpm: cfg.rate_limit.image_rpm,
    gemini_video_rpm: cfg.rate_limit.video_rpm,
    gemini_request_gap: cfg.rate_limit.request_gap_seconds,
    storyboard_max_workers: cfg.performance.storyboard_max_workers,
    video_max_workers: cfg.performance.video_max_workers,
  };
}

function statusBadge(source: string): string {
  if (source === "override") return "UI 覆盖";
  if (source === "env") return ".env";
  return "未设置";
}

export function SystemConfigPage() {
  const [, navigate] = useLocation();
  const [data, setData] = useState<GetSystemConfigResponse | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [geminiKeyInput, setGeminiKeyInput] = useState("");
  const [anthropicKeyInput, setAnthropicKeyInput] = useState("");
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await API.getSystemConfig();
      setData(res);
      setDraft(buildDraft(res));
    } catch (err) {
      useAppStore.getState().pushToast(`加载失败: ${(err as Error).message}`, "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await load();
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const audioEditable = draft?.video_backend === "vertex";
  const audioEffective = audioEditable ? (draft?.video_generate_audio ?? true) : true;

  const dirty = useMemo(() => {
    if (!data || !draft) return false;
    const cfg = data.config;
    return (
      draft.image_backend !== cfg.image_backend ||
      draft.video_backend !== cfg.video_backend ||
      draft.image_model !== cfg.image_model ||
      draft.video_model !== cfg.video_model ||
      draft.video_generate_audio !== cfg.video_generate_audio ||
      draft.gemini_image_rpm !== cfg.rate_limit.image_rpm ||
      draft.gemini_video_rpm !== cfg.rate_limit.video_rpm ||
      draft.gemini_request_gap !== cfg.rate_limit.request_gap_seconds ||
      draft.storyboard_max_workers !== cfg.performance.storyboard_max_workers ||
      draft.video_max_workers !== cfg.performance.video_max_workers
    );
  }, [data, draft]);

  const handleSave = useCallback(async () => {
    if (!data || !draft || saving) return;
    const cfg = data.config;

    const patch: SystemConfigPatch = {};

    if (draft.image_backend !== cfg.image_backend) patch.image_backend = draft.image_backend;
    if (draft.video_backend !== cfg.video_backend) patch.video_backend = draft.video_backend;
    if (draft.image_model !== cfg.image_model) patch.image_model = draft.image_model;
    if (draft.video_model !== cfg.video_model) patch.video_model = draft.video_model;
    if (draft.video_generate_audio !== cfg.video_generate_audio) patch.video_generate_audio = draft.video_generate_audio;

    if (draft.gemini_image_rpm !== cfg.rate_limit.image_rpm) patch.gemini_image_rpm = draft.gemini_image_rpm;
    if (draft.gemini_video_rpm !== cfg.rate_limit.video_rpm) patch.gemini_video_rpm = draft.gemini_video_rpm;
    if (draft.gemini_request_gap !== cfg.rate_limit.request_gap_seconds) patch.gemini_request_gap = draft.gemini_request_gap;

    if (draft.storyboard_max_workers !== cfg.performance.storyboard_max_workers) patch.storyboard_max_workers = draft.storyboard_max_workers;
    if (draft.video_max_workers !== cfg.performance.video_max_workers) patch.video_max_workers = draft.video_max_workers;

    const geminiKey = geminiKeyInput.trim();
    const anthropicKey = anthropicKeyInput.trim();
    if (geminiKey) patch.gemini_api_key = geminiKey;
    if (anthropicKey) patch.anthropic_api_key = anthropicKey;

    setSaving(true);
    try {
      const res = await API.updateSystemConfig(patch);
      setData(res);
      setDraft(buildDraft(res));
      setGeminiKeyInput("");
      setAnthropicKeyInput("");
      useAppStore.getState().pushToast("系统配置已保存并立即生效", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`保存失败: ${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [anthropicKeyInput, data, draft, geminiKeyInput, saving]);

  const handleClearKey = useCallback(async (type: "gemini" | "anthropic") => {
    setSaving(true);
    try {
      const patch: SystemConfigPatch =
        type === "gemini" ? { gemini_api_key: "" } : { anthropic_api_key: "" };
      const res = await API.updateSystemConfig(patch);
      setData(res);
      setDraft(buildDraft(res));
      if (type === "gemini") setGeminiKeyInput("");
      if (type === "anthropic") setAnthropicKeyInput("");
      useAppStore.getState().pushToast("已清除 UI 覆盖，恢复默认来源", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`操作失败: ${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }, []);

  const handleUploadVertex = useCallback(async (file: File) => {
    setUploading(true);
    try {
      const res = await API.uploadVertexCredentials(file);
      setData(res);
      setDraft(buildDraft(res));
      useAppStore.getState().pushToast("Vertex 凭证已上传", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`上传失败: ${(err as Error).message}`, "error");
    } finally {
      setUploading(false);
    }
  }, []);

  if (loading || !data || !draft) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <header className="border-b border-gray-800 px-6 py-4">
          <div className="mx-auto flex max-w-5xl items-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/app/projects")}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-200 hover:border-gray-700 hover:bg-gray-800"
            >
              <ChevronLeft className="h-4 w-4" />
              返回
            </button>
            <h1 className="text-lg font-semibold text-gray-100">系统配置</h1>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-14">
          <div className="flex items-center gap-2 text-gray-400">
            <Loader2 className="h-5 w-5 animate-spin text-indigo-400" />
            加载配置中...
          </div>
        </main>
      </div>
    );
  }

  const imageModels = data.options.image_models;
  const videoModels = data.options.video_models;
  const vertexStatus = data.config.vertex_credentials;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/app/projects")}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-200 hover:border-gray-700 hover:bg-gray-800"
              aria-label="返回项目大厅"
            >
              <ChevronLeft className="h-4 w-4" />
              返回
            </button>
            <div>
              <h1 className="text-lg font-semibold text-gray-100">系统配置</h1>
              <p className="text-xs text-gray-500">
                修改后立即生效（无需重启服务）
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || uploading || (!dirty && !geminiKeyInput.trim() && !anthropicKeyInput.trim())}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-6 px-6 py-8">
        {/* Secrets */}
        <section className="rounded-2xl border border-gray-800 bg-gradient-to-b from-gray-900 to-gray-900/60 p-6 shadow-xl shadow-black/20">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-indigo-500/10 p-2 text-indigo-200">
                <KeyRound className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-gray-100">密钥</h2>
                <p className="text-xs text-gray-500">
                  密钥仅脱敏显示；输入新值后点击保存
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void load()}
              className="text-xs text-gray-400 hover:text-gray-200"
            >
              刷新
            </button>
          </div>

          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <div className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-gray-100">Gemini API Key</div>
                  <div className="mt-1 text-xs text-gray-500">
                    当前：{data.config.gemini_api_key.masked ?? "未设置"} ·{" "}
                    <span className="text-gray-400">{statusBadge(data.config.gemini_api_key.source)}</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void handleClearKey("gemini")}
                  disabled={saving}
                  className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-60"
                >
                  清除覆盖
                </button>
              </div>
              <input
                value={geminiKeyInput}
                onChange={(e) => setGeminiKeyInput(e.target.value)}
                placeholder="输入新的 GEMINI_API_KEY（留空不修改）"
                className="mt-3 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none"
                autoComplete="off"
                spellCheck={false}
              />
            </div>

            <div className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-gray-100">Anthropic API Key</div>
                  <div className="mt-1 text-xs text-gray-500">
                    当前：{data.config.anthropic_api_key.masked ?? "未设置"} ·{" "}
                    <span className="text-gray-400">{statusBadge(data.config.anthropic_api_key.source)}</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void handleClearKey("anthropic")}
                  disabled={saving}
                  className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-60"
                >
                  清除覆盖
                </button>
              </div>
              <input
                value={anthropicKeyInput}
                onChange={(e) => setAnthropicKeyInput(e.target.value)}
                placeholder="输入新的 ANTHROPIC_API_KEY（留空不修改）"
                className="mt-3 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          </div>
        </section>

        {/* Backends + Vertex creds */}
        <section className="rounded-2xl border border-gray-800 bg-gray-900/60 p-6 shadow-xl shadow-black/20">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-emerald-500/10 p-2 text-emerald-200">
              <Cpu className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-100">后端与模型</h2>
              <p className="text-xs text-gray-500">图片/视频后端可分别配置</p>
            </div>
          </div>

          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <div className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="text-sm font-medium text-gray-100">图片后端</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {(["aistudio", "vertex"] as const).map((b) => (
                  <label
                    key={b}
                    className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                      draft.image_backend === b
                        ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                        : "border-gray-800 bg-gray-950 text-gray-300 hover:border-gray-700"
                    }`}
                  >
                    <span>{b === "aistudio" ? "AI Studio" : "Vertex AI"}</span>
                    <input
                      type="radio"
                      name="image_backend"
                      checked={draft.image_backend === b}
                      onChange={() => setDraft((s) => (s ? { ...s, image_backend: b } : s))}
                      className="sr-only"
                    />
                  </label>
                ))}
              </div>

              <div className="mt-4 text-sm font-medium text-gray-100">图片模型</div>
              <select
                value={draft.image_model}
                onChange={(e) => setDraft((s) => (s ? { ...s, image_model: e.target.value } : s))}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
              >
                {imageModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>

            <div className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="text-sm font-medium text-gray-100">视频后端</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {(["aistudio", "vertex"] as const).map((b) => (
                  <label
                    key={b}
                    className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                      draft.video_backend === b
                        ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                        : "border-gray-800 bg-gray-950 text-gray-300 hover:border-gray-700"
                    }`}
                  >
                    <span>{b === "aistudio" ? "AI Studio" : "Vertex AI"}</span>
                    <input
                      type="radio"
                      name="video_backend"
                      checked={draft.video_backend === b}
                      onChange={() => setDraft((s) => (s ? { ...s, video_backend: b } : s))}
                      className="sr-only"
                    />
                  </label>
                ))}
              </div>

              <div className="mt-4 text-sm font-medium text-gray-100">视频模型</div>
              <select
                value={draft.video_model}
                onChange={(e) => setDraft((s) => (s ? { ...s, video_model: e.target.value } : s))}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
              >
                {videoModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>

              <div className="mt-4 flex items-start justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/70 px-3 py-2">
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={draft.video_generate_audio}
                    onChange={(e) =>
                      setDraft((s) =>
                        s ? { ...s, video_generate_audio: e.target.checked } : s
                      )
                    }
                    disabled={!audioEditable}
                    className="mt-1 h-4 w-4 rounded border-gray-700 bg-gray-950"
                  />
                  <span className="text-sm text-gray-200">
                    生成音频
                    <span className="ml-2 text-xs text-gray-500">
                      {audioEditable
                        ? "（仅 Vertex 可关闭）"
                        : "（AI Studio 固定开启）"}
                    </span>
                  </span>
                </label>
                <span className="text-xs text-gray-500">
                  生效：{audioEffective ? "开启" : "关闭"}
                </span>
              </div>
            </div>
          </div>

          <div className="mt-5 rounded-xl border border-gray-800 bg-gray-950/30 p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-gray-100">Vertex 凭证</div>
                <div className="mt-1 text-xs text-gray-500">
                  {vertexStatus.is_set ? (
                    <>
                      已上传：<span className="text-gray-300">{vertexStatus.filename}</span>
                      {vertexStatus.project_id ? (
                        <>
                          {" "}· 项目：<span className="text-gray-300">{vertexStatus.project_id}</span>
                        </>
                      ) : null}
                    </>
                  ) : (
                    <>
                      未上传 · 切换到 Vertex 前请先上传 JSON 凭证
                    </>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={uploading}
                  className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 hover:border-gray-700 hover:bg-gray-900 disabled:cursor-not-allowed disabled:opacity-60"
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
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (!file) return;
                    void handleUploadVertex(file);
                  }}
                />
              </div>
            </div>
          </div>
        </section>

        {/* Rate limit + performance */}
        <section className="rounded-2xl border border-gray-800 bg-gray-900/60 p-6 shadow-xl shadow-black/20">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-amber-500/10 p-2 text-amber-200">
              <Gauge className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-100">限流</h2>
              <p className="text-xs text-gray-500">RPM、请求间隔、并发等运行时参数</p>
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-3">
            <label className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="text-sm font-medium text-gray-100">图片 RPM</div>
              <input
                type="number"
                min={0}
                value={draft.gemini_image_rpm}
                onChange={(e) => setDraft((s) => (s ? { ...s, gemini_image_rpm: Number(e.target.value) } : s))}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
              />
              <div className="mt-2 text-xs text-gray-500">设置为 0 表示不限制</div>
            </label>

            <label className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="text-sm font-medium text-gray-100">视频 RPM</div>
              <input
                type="number"
                min={0}
                value={draft.gemini_video_rpm}
                onChange={(e) => setDraft((s) => (s ? { ...s, gemini_video_rpm: Number(e.target.value) } : s))}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
              />
              <div className="mt-2 text-xs text-gray-500">设置为 0 表示不限制</div>
            </label>

            <label className="rounded-xl border border-gray-800 bg-gray-950/30 p-4">
              <div className="text-sm font-medium text-gray-100">请求间隔（秒）</div>
              <input
                type="number"
                min={0}
                step="0.1"
                value={draft.gemini_request_gap}
                onChange={(e) => setDraft((s) => (s ? { ...s, gemini_request_gap: Number(e.target.value) } : s))}
                className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
              />
              <div className="mt-2 text-xs text-gray-500">控制连续请求的最小间隔</div>
            </label>
          </div>

          <details className="mt-5 rounded-xl border border-gray-800 bg-gray-950/30 p-4">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-gray-100">
              <span className="inline-flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-gray-400" />
                高级配置（并发）
              </span>
              <span className="text-xs text-gray-500">STORYBOARD/VIDEO workers</span>
            </summary>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className="rounded-xl border border-gray-800 bg-gray-950 p-4">
                <div className="text-sm font-medium text-gray-100">分镜最大并发</div>
                <input
                  type="number"
                  min={1}
                  value={draft.storyboard_max_workers}
                  onChange={(e) => setDraft((s) => (s ? { ...s, storyboard_max_workers: Number(e.target.value) } : s))}
                  className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
                />
              </label>

              <label className="rounded-xl border border-gray-800 bg-gray-950 p-4">
                <div className="text-sm font-medium text-gray-100">视频最大并发</div>
                <input
                  type="number"
                  min={1}
                  value={draft.video_max_workers}
                  onChange={(e) => setDraft((s) => (s ? { ...s, video_max_workers: Number(e.target.value) } : s))}
                  className="mt-2 w-full rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none"
                />
              </label>
            </div>
            <div className="mt-3 text-xs text-gray-500">
              修改后仅影响后续任务；不强制中断已在途生成。
            </div>
          </details>
        </section>
      </main>
    </div>
  );
}

