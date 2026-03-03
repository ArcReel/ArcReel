import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "wouter";
import {
  ChevronDown,
  ChevronLeft,
  Cpu,
  Gauge,
  KeyRound,
  Loader2,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
} from "lucide-react";
import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
import GoogleColor from "@lobehub/icons/es/Google/components/Color";
import VertexAIColor from "@lobehub/icons/es/VertexAI/components/Color";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type {
  GetSystemConfigResponse,
  SystemBackend,
  SystemConfigPatch,
  SystemConnectionTestResponse,
} from "@/types";

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

type ProviderTestState =
  | { status: "idle" }
  | { status: "success"; result: SystemConnectionTestResponse }
  | { status: "error"; message: string };

type SaveSection = "secrets" | "backend" | "limits";

const SECRET_PATCH_KEYS = [
  "gemini_api_key",
  "anthropic_api_key",
  "anthropic_base_url",
] as const satisfies readonly (keyof SystemConfigPatch)[];

const BACKEND_PATCH_KEYS = [
  "image_backend",
  "video_backend",
  "image_model",
  "video_model",
  "video_generate_audio",
] as const satisfies readonly (keyof SystemConfigPatch)[];

const LIMIT_PATCH_KEYS = [
  "gemini_image_rpm",
  "gemini_video_rpm",
  "gemini_request_gap",
  "storyboard_max_workers",
  "video_max_workers",
] as const satisfies readonly (keyof SystemConfigPatch)[];

const BACKEND_DRAFT_KEYS = [
  "image_backend",
  "video_backend",
  "image_model",
  "video_model",
  "video_generate_audio",
] as const satisfies readonly (keyof DraftState)[];

const LIMIT_DRAFT_KEYS = [
  "gemini_image_rpm",
  "gemini_video_rpm",
  "gemini_request_gap",
  "storyboard_max_workers",
  "video_max_workers",
] as const satisfies readonly (keyof DraftState)[];

const sectionClassName =
  "rounded-2xl border border-gray-800 bg-gray-900/90 p-6 shadow-xl shadow-black/20";
const cardClassName = "rounded-xl border border-gray-800 bg-gray-950/40 p-4";
const inputClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none";
const selectClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500/60 focus:outline-none";
const secondaryButtonClassName =
  "inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80 disabled:cursor-not-allowed disabled:opacity-60";
const saveButtonClassName =
  "inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60";
const sectionIconFrameClassName =
  "rounded-xl border border-gray-800 bg-gray-950/70 p-2 text-gray-300";
const vendorIconFrameClassName =
  "rounded-2xl border border-gray-800 bg-gray-900 px-3 py-3 shadow-inner shadow-white/5";
const infoStripClassName =
  "mt-3 flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2";
const successNoteClassName =
  "mt-3 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2 text-xs text-gray-300";
const errorNoteClassName =
  "mt-3 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-100";

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
  if (source === "override") return "";
  if (source === "env") return ".env";
  return "未设置";
}

function buildPatch(
  data: GetSystemConfigResponse,
  draft: DraftState,
  geminiKeyInput: string,
  anthropicKeyInput: string,
  anthropicBaseUrlInput: string,
): SystemConfigPatch {
  const cfg = data.config;
  const patch: SystemConfigPatch = {};

  if (draft.image_backend !== cfg.image_backend) patch.image_backend = draft.image_backend;
  if (draft.video_backend !== cfg.video_backend) patch.video_backend = draft.video_backend;
  if (draft.image_model !== cfg.image_model) patch.image_model = draft.image_model;
  if (draft.video_model !== cfg.video_model) patch.video_model = draft.video_model;
  if (draft.video_generate_audio !== cfg.video_generate_audio) {
    patch.video_generate_audio = draft.video_generate_audio;
  }
  if (draft.gemini_image_rpm !== cfg.rate_limit.image_rpm) {
    patch.gemini_image_rpm = draft.gemini_image_rpm;
  }
  if (draft.gemini_video_rpm !== cfg.rate_limit.video_rpm) {
    patch.gemini_video_rpm = draft.gemini_video_rpm;
  }
  if (draft.gemini_request_gap !== cfg.rate_limit.request_gap_seconds) {
    patch.gemini_request_gap = draft.gemini_request_gap;
  }
  if (draft.storyboard_max_workers !== cfg.performance.storyboard_max_workers) {
    patch.storyboard_max_workers = draft.storyboard_max_workers;
  }
  if (draft.video_max_workers !== cfg.performance.video_max_workers) {
    patch.video_max_workers = draft.video_max_workers;
  }

  const geminiKey = geminiKeyInput.trim();
  const anthropicKey = anthropicKeyInput.trim();
  const anthropicBaseUrl = anthropicBaseUrlInput.trim();
  if (geminiKey) patch.gemini_api_key = geminiKey;
  if (anthropicKey) patch.anthropic_api_key = anthropicKey;
  if (anthropicBaseUrl) patch.anthropic_base_url = anthropicBaseUrl;

  return patch;
}

function hasPatch(patch: SystemConfigPatch): boolean {
  return Object.keys(patch).length > 0;
}

function pickPatch(
  patch: SystemConfigPatch | null,
  keys: readonly (keyof SystemConfigPatch)[],
): SystemConfigPatch {
  const picked: Record<string, unknown> = {};
  if (!patch) return picked as SystemConfigPatch;
  for (const key of keys) {
    const value = patch[key];
    if (value !== undefined) {
      picked[key] = value;
    }
  }
  return picked as SystemConfigPatch;
}

function mergeDraftKeys(
  current: DraftState | null,
  responseDraft: DraftState,
  keys: readonly (keyof DraftState)[],
): DraftState {
  if (!current) return responseDraft;
  const next: DraftState = { ...current };
  for (const key of keys) {
    next[key] = responseDraft[key] as never;
  }
  return next;
}

export function SystemConfigPage() {
  const [, navigate] = useLocation();
  const [data, setData] = useState<GetSystemConfigResponse | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savingSection, setSavingSection] = useState<SaveSection | null>(null);
  const [uploading, setUploading] = useState(false);
  const [testingProvider, setTestingProvider] = useState<SystemBackend | null>(null);
  const [aistudioTestState, setAistudioTestState] = useState<ProviderTestState>({
    status: "idle",
  });
  const [vertexTestState, setVertexTestState] = useState<ProviderTestState>({
    status: "idle",
  });
  const [geminiKeyInput, setGeminiKeyInput] = useState("");
  const [anthropicKeyInput, setAnthropicKeyInput] = useState("");
  const [anthropicBaseUrlInput, setAnthropicBaseUrlInput] = useState("");
  const [limitsExpanded, setLimitsExpanded] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await API.getSystemConfig();
      setData(res);
      setDraft(buildDraft(res));
      setGeminiKeyInput("");
      setAnthropicKeyInput("");
      setAnthropicBaseUrlInput("");
    } catch (err) {
      const message = (err as Error).message;
      setLoadError(message);
      useAppStore.getState().pushToast(`加载失败: ${message}`, "error");
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

  const pendingPatch = useMemo(() => {
    if (!data || !draft) return null;
    return buildPatch(data, draft, geminiKeyInput, anthropicKeyInput, anthropicBaseUrlInput);
  }, [anthropicBaseUrlInput, anthropicKeyInput, data, draft, geminiKeyInput]);

  const secretPatch = useMemo(
    () => pickPatch(pendingPatch, SECRET_PATCH_KEYS),
    [pendingPatch],
  );
  const backendPatch = useMemo(
    () => pickPatch(pendingPatch, BACKEND_PATCH_KEYS),
    [pendingPatch],
  );
  const limitPatch = useMemo(
    () => pickPatch(pendingPatch, LIMIT_PATCH_KEYS),
    [pendingPatch],
  );

  const hasSecretChanges = hasPatch(secretPatch);
  const hasBackendChanges = hasPatch(backendPatch);
  const hasLimitChanges = hasPatch(limitPatch);

  const handleSaveSection = useCallback(
    async (section: SaveSection) => {
      if (saving) return;

      const sectionPatch =
        section === "secrets"
          ? secretPatch
          : section === "backend"
            ? backendPatch
            : limitPatch;
      if (!hasPatch(sectionPatch)) return;

      setSaving(true);
      setSavingSection(section);
      try {
        const res = await API.updateSystemConfig(sectionPatch);
        setData(res);

        if (section === "backend") {
          setDraft((current) =>
            mergeDraftKeys(current, buildDraft(res), BACKEND_DRAFT_KEYS)
          );
        } else if (section === "limits") {
          setDraft((current) =>
            mergeDraftKeys(current, buildDraft(res), LIMIT_DRAFT_KEYS)
          );
        }

        if (section === "secrets") {
          if ("gemini_api_key" in sectionPatch) setGeminiKeyInput("");
          if ("anthropic_api_key" in sectionPatch) setAnthropicKeyInput("");
          if ("anthropic_base_url" in sectionPatch) setAnthropicBaseUrlInput("");
        }

        useAppStore.getState().pushToast("系统配置已保存并立即生效", "success");
      } catch (err) {
        useAppStore.getState().pushToast(`保存失败: ${(err as Error).message}`, "error");
      } finally {
        setSaving(false);
        setSavingSection(null);
      }
    },
    [backendPatch, limitPatch, saving, secretPatch],
  );

  const handleClearKey = useCallback(async (type: "gemini" | "anthropic") => {
    setSaving(true);
    setSavingSection("secrets");
    try {
      const patch: SystemConfigPatch =
        type === "gemini" ? { gemini_api_key: "" } : { anthropic_api_key: "" };
      const res = await API.updateSystemConfig(patch);
      setData(res);
      if (type === "gemini") {
        setGeminiKeyInput("");
        setAistudioTestState({ status: "idle" });
      }
      if (type === "anthropic") setAnthropicKeyInput("");
      useAppStore.getState().pushToast("已清除当前配置，恢复默认来源", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`操作失败: ${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
      setSavingSection(null);
    }
  }, []);

  const handleClearAnthropicBaseUrl = useCallback(async () => {
    setSaving(true);
    setSavingSection("secrets");
    try {
      const res = await API.updateSystemConfig({ anthropic_base_url: "" });
      setData(res);
      setAnthropicBaseUrlInput("");
      useAppStore.getState().pushToast("已清除 Anthropic Base URL 覆盖", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`操作失败: ${(err as Error).message}`, "error");
    } finally {
      setSaving(false);
      setSavingSection(null);
    }
  }, []);

  const handleUploadVertex = useCallback(async (file: File) => {
    setUploading(true);
    try {
      const res = await API.uploadVertexCredentials(file);
      setData(res);
      setDraft(buildDraft(res));
      setVertexTestState({ status: "idle" });
      useAppStore.getState().pushToast("Vertex 凭证已上传", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`上传失败: ${(err as Error).message}`, "error");
    } finally {
      setUploading(false);
    }
  }, []);

  const handleTestConnection = useCallback(async (provider: SystemBackend) => {
    if (!draft) return;

    setTestingProvider(provider);
    if (provider === "aistudio") {
      setAistudioTestState({ status: "idle" });
    } else {
      setVertexTestState({ status: "idle" });
    }

    try {
      const res = await API.testSystemConnection({
        provider,
        image_backend: draft.image_backend,
        video_backend: draft.video_backend,
        image_model: draft.image_model,
        video_model: draft.video_model,
        gemini_api_key: provider === "aistudio" ? geminiKeyInput.trim() || null : null,
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
  }, [draft, geminiKeyInput]);

  if (loading) {
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

  if (!data || !draft) {
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
        <main className="mx-auto max-w-3xl px-6 py-14">
          <div className={sectionClassName}>
            <div className="text-sm font-medium text-rose-200">配置加载失败</div>
            <p className="mt-2 text-sm text-gray-300">
              {loadError ?? "无法获取系统配置，请稍后重试。"}
            </p>
            <div className="mt-5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => void load()}
                className={saveButtonClassName}
              >
                <Loader2 className="h-4 w-4" />
                重试加载
              </button>
              <button
                type="button"
                onClick={() => navigate("/app/projects")}
                className={secondaryButtonClassName}
              >
                返回项目页
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  const imageModels = data.options.image_models;
  const videoModels = data.options.video_models;
  const vertexStatus = data.config.vertex_credentials;
  const anthropicBaseUrlStatus = data.config.anthropic_base_url;
  const anthropicSourceBadge = statusBadge(data.config.anthropic_api_key.source);
  const geminiSourceBadge = statusBadge(data.config.gemini_api_key.source);
  const geminiKeyAvailable = Boolean(geminiKeyInput.trim() || data.config.gemini_api_key.is_set);

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
              <p className="text-xs text-gray-500">修改后点击区块右下角保存并立即生效（无需重启服务）</p>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-6 px-6 py-8">
        <section className={sectionClassName}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className={sectionIconFrameClassName}>
                <KeyRound className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-gray-100">密钥与凭证</h2>
                <p className="text-xs text-gray-500">密钥与 Vertex 凭证统一管理，修改后即时生效</p>
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

          <div className="mt-5 space-y-5">
            <div className={cardClassName}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className={vendorIconFrameClassName}>
                    <ClaudeColor size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-100">Anthropic API Key</div>
                    <div className="mt-1 text-xs text-gray-500">
                      当前：{data.config.anthropic_api_key.masked ?? "未设置"}
                      {anthropicSourceBadge ? (
                        <>
                          {" "}
                          · <span className="text-gray-400">{anthropicSourceBadge}</span>
                        </>
                      ) : null}
                    </div>
                    <div className="mt-1 text-xs text-gray-600">
                      用于 ArcReel 智能体 (Claude Agent SDK)；如果需使用代理网关，可额外设置 Base URL。
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void handleClearKey("anthropic")}
                  disabled={saving}
                  className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-60"
                >
                  清除 Key
                </button>
              </div>
              <input
                value={anthropicKeyInput}
                onChange={(e) => setAnthropicKeyInput(e.target.value)}
                placeholder="输入新的 ANTHROPIC_API_KEY（留空不修改）"
                className={`mt-3 ${inputClassName}`}
                autoComplete="off"
                spellCheck={false}
              />
              <div className="mt-4 border-t border-gray-800 pt-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-gray-100">Base URL</div>
                    <div className="mt-1 text-xs text-gray-400">
                      当前 Base URL：{anthropicBaseUrlStatus.value ?? "官方默认"}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleClearAnthropicBaseUrl()}
                    disabled={saving}
                    className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-60"
                  >
                    清除 URL
                  </button>
                </div>
                <input
                  value={anthropicBaseUrlInput}
                  onChange={(e) => setAnthropicBaseUrlInput(e.target.value)}
                  placeholder="https://proxy.example.com"
                  className={`mt-3 ${inputClassName}`}
                  autoComplete="off"
                  spellCheck={false}
                />
                <div className="mt-2 text-xs text-gray-500">
                  可选。留空时使用 Anthropic 官方默认地址；配置代理时填写你的网关地址。
                </div>
              </div>
            </div>

            <div className={cardClassName}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className={vendorIconFrameClassName}>
                    <GoogleColor size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-100">Gemini API Key</div>
                    <div className="mt-1 text-xs text-gray-500">
                      当前：{data.config.gemini_api_key.masked ?? "未设置"}
                      {geminiSourceBadge ? (
                        <>
                          {" "}
                          · <span className="text-gray-400">{geminiSourceBadge}</span>
                        </>
                      ) : null}
                    </div>
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
                onChange={(e) => {
                  setGeminiKeyInput(e.target.value);
                  setAistudioTestState({ status: "idle" });
                }}
                placeholder="输入新的 GEMINI_API_KEY（留空不修改）"
                className={`mt-3 ${inputClassName}`}
                autoComplete="off"
                spellCheck={false}
              />
              <div className={infoStripClassName}>
                <div className="text-xs text-gray-300">
                  使用当前配置中的模型进行校验；若上方填入新 key，则优先验证该 key（不会覆盖已保存配置）。
                </div>
                <button
                  type="button"
                  onClick={() => void handleTestConnection("aistudio")}
                  disabled={
                    saving ||
                    uploading ||
                    testingProvider !== null ||
                    !geminiKeyAvailable
                  }
                  className={secondaryButtonClassName}
                >
                  {testingProvider === "aistudio" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ShieldCheck className="h-4 w-4" />
                  )}
                  {testingProvider === "aistudio" ? "验证中..." : "验证 Gemini 连接"}
                </button>
              </div>
              {aistudioTestState.status === "success" ? (
                <div className={successNoteClassName}>
                  {aistudioTestState.result.message}
                </div>
              ) : null}
              {aistudioTestState.status === "error" ? (
                <div className={errorNoteClassName}>
                  {aistudioTestState.message}
                </div>
              ) : null}
            </div>

            <div className={cardClassName}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className={vendorIconFrameClassName}>
                    <VertexAIColor size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-100">Vertex 凭证</div>
                    <div className="mt-1 text-xs text-gray-400">
                      {vertexStatus.is_set ? (
                        <>
                          已上传：<span className="text-gray-200">{vertexStatus.filename}</span>
                          {vertexStatus.project_id ? (
                            <>
                              {" "}· 项目：<span className="text-gray-200">{vertexStatus.project_id}</span>
                            </>
                          ) : null}
                        </>
                      ) : (
                        <>未上传 · 切换到 Vertex 前请先上传 JSON 凭证</>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleTestConnection("vertex")}
                    disabled={
                      uploading ||
                      saving ||
                      testingProvider !== null ||
                      !vertexStatus.is_set
                    }
                    className={secondaryButtonClassName}
                  >
                    {testingProvider === "vertex" ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ShieldCheck className="h-4 w-4" />
                    )}
                    {testingProvider === "vertex" ? "测试中..." : "测试 Vertex"}
                  </button>
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
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (!file) return;
                      void handleUploadVertex(file);
                    }}
                  />
                </div>
              </div>
              {vertexTestState.status === "success" ? (
                <div className="mt-4 rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2 text-xs text-gray-300">
                  {vertexTestState.result.message}
                  {vertexTestState.result.project_id ? (
                    <span className="text-gray-500">
                      {" "}· 项目 {vertexTestState.result.project_id}
                    </span>
                  ) : null}
                </div>
              ) : null}
              {vertexTestState.status === "error" ? (
                <div className="mt-4 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-100">
                  {vertexTestState.message}
                </div>
              ) : null}
            </div>
          </div>

          {hasSecretChanges && (
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => void handleSaveSection("secrets")}
                disabled={saving || uploading || !hasSecretChanges}
                className={saveButtonClassName}
              >
                {savingSection === "secrets" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {savingSection === "secrets" ? "保存中..." : "保存密钥与凭证"}
              </button>
            </div>
          )}
        </section>

        <section className={sectionClassName}>
          <div className="flex items-center gap-3">
            <div className={sectionIconFrameClassName}>
              <Cpu className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-100">后端与模型</h2>
              <p className="text-xs text-gray-500">图片/视频后端可分别配置</p>
            </div>
          </div>

          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <div className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">图片后端</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {(["aistudio", "vertex"] as const).map((b) => (
                  <label
                    key={b}
                    className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                      draft.image_backend === b
                        ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                        : "border-gray-800 bg-gray-900/80 text-gray-300 hover:border-gray-700 hover:bg-gray-900"
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
                className={`mt-2 ${selectClassName}`}
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
                      draft.video_backend === b
                        ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                        : "border-gray-800 bg-gray-900/80 text-gray-300 hover:border-gray-700 hover:bg-gray-900"
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
                className={`mt-2 ${selectClassName}`}
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
                    checked={draft.video_generate_audio}
                    onChange={(e) =>
                      setDraft((s) =>
                        s ? { ...s, video_generate_audio: e.target.checked } : s
                      )
                    }
                    disabled={!audioEditable}
                    className="mt-1 h-4 w-4 rounded border-gray-700 bg-gray-900"
                  />
                  <span className="text-sm text-gray-200">
                    生成音频
                    <span className="ml-2 text-xs text-gray-500">
                      {audioEditable ? "（仅 Vertex 可关闭）" : "（AI Studio 固定开启）"}
                    </span>
                  </span>
                </label>
                <span className="text-xs text-gray-500">
                  生效：{audioEffective ? "开启" : "关闭"}
                </span>
              </div>
            </div>
          </div>

          {hasBackendChanges && (
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => void handleSaveSection("backend")}
                disabled={saving || uploading || !hasBackendChanges}
                className={saveButtonClassName}
              >
                {savingSection === "backend" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {savingSection === "backend" ? "保存中..." : "保存后端与模型"}
              </button>
            </div>
          )}

        </section>

        <section className={sectionClassName}>
          <div className="flex items-center gap-3">
            <div className={sectionIconFrameClassName}>
              <Gauge className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-100">限流</h2>
              <p className="text-xs text-gray-500">RPM、请求间隔、并发等运行时参数</p>
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-3">
            <label className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">图片 RPM</div>
              <input
                type="number"
                min={0}
                value={draft.gemini_image_rpm}
                onChange={(e) =>
                  setDraft((s) => (s ? { ...s, gemini_image_rpm: Number(e.target.value) } : s))
                }
                className={`mt-2 ${inputClassName}`}
              />
              <div className="mt-2 text-xs text-gray-500">设置为 0 表示不限制</div>
            </label>

            <label className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">视频 RPM</div>
              <input
                type="number"
                min={0}
                value={draft.gemini_video_rpm}
                onChange={(e) =>
                  setDraft((s) => (s ? { ...s, gemini_video_rpm: Number(e.target.value) } : s))
                }
                className={`mt-2 ${inputClassName}`}
              />
              <div className="mt-2 text-xs text-gray-500">设置为 0 表示不限制</div>
            </label>

            <label className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">请求间隔（秒）</div>
              <input
                type="number"
                min={0}
                step="0.1"
                value={draft.gemini_request_gap}
                onChange={(e) =>
                  setDraft((s) => (s ? { ...s, gemini_request_gap: Number(e.target.value) } : s))
                }
                className={`mt-2 ${inputClassName}`}
              />
              <div className="mt-2 text-xs text-gray-500">控制连续请求的最小间隔</div>
            </label>
          </div>

          <details
            open={limitsExpanded}
            onToggle={(e) => setLimitsExpanded(e.currentTarget.open)}
            className="mt-5 rounded-xl border border-gray-800 bg-gray-950/40 p-4"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-gray-100">
              <span className="inline-flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-gray-400" />
                高级配置（并发）
              </span>
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-gray-800 bg-gray-900 text-gray-500">
                <ChevronDown
                  className={`h-4 w-4 transition-transform duration-200 ${
                    limitsExpanded ? "rotate-180 text-gray-200" : ""
                  }`}
                />
              </span>
            </summary>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className={cardClassName}>
                <div className="text-sm font-medium text-gray-100">分镜最大并发</div>
                <input
                  type="number"
                  min={1}
                  value={draft.storyboard_max_workers}
                  onChange={(e) =>
                    setDraft((s) =>
                      s ? { ...s, storyboard_max_workers: Number(e.target.value) } : s
                    )
                  }
                  className={`mt-2 ${inputClassName}`}
                />
              </label>

              <label className={cardClassName}>
                <div className="text-sm font-medium text-gray-100">视频最大并发</div>
                <input
                  type="number"
                  min={1}
                  value={draft.video_max_workers}
                  onChange={(e) =>
                    setDraft((s) =>
                      s ? { ...s, video_max_workers: Number(e.target.value) } : s
                    )
                  }
                  className={`mt-2 ${inputClassName}`}
                />
              </label>
            </div>
            <div className="mt-3 text-xs text-gray-500">
              修改后仅影响后续任务；不强制中断已在途生成。
            </div>
          </details>

          {hasLimitChanges && (
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => void handleSaveSection("limits")}
                disabled={saving || uploading || !hasLimitChanges}
                className={saveButtonClassName}
              >
                {savingSection === "limits" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {savingSection === "limits" ? "保存中..." : "保存限流配置"}
              </button>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
