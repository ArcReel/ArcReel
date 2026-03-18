import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Eye, EyeOff, Loader2, SlidersHorizontal, X } from "lucide-react";
import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponseNew, SystemConfigPatchNew } from "@/types";
import { TabSaveFooter } from "./TabSaveFooter";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface AgentDraft {
  anthropicKey: string;        // new API key input (empty = don't change)
  anthropicBaseUrl: string;    // in-place editing; empty = clear
  anthropicModel: string;      // in-place editing; empty = clear
  haikuModel: string;
  opusModel: string;
  sonnetModel: string;
  subagentModel: string;
}

function buildDraft(data: GetSystemConfigResponseNew): AgentDraft {
  const s = data.settings;
  return {
    anthropicKey: "",
    anthropicBaseUrl: s.anthropic_base_url ?? "",
    anthropicModel: s.anthropic_model ?? "",
    haikuModel: s.anthropic_default_haiku_model ?? "",
    opusModel: s.anthropic_default_opus_model ?? "",
    sonnetModel: s.anthropic_default_sonnet_model ?? "",
    subagentModel: s.claude_code_subagent_model ?? "",
  };
}

function deepEqual(a: AgentDraft, b: AgentDraft): boolean {
  return (
    a.anthropicKey === b.anthropicKey &&
    a.anthropicBaseUrl === b.anthropicBaseUrl &&
    a.anthropicModel === b.anthropicModel &&
    a.haikuModel === b.haikuModel &&
    a.opusModel === b.opusModel &&
    a.sonnetModel === b.sonnetModel &&
    a.subagentModel === b.subagentModel
  );
}

function buildPatch(draft: AgentDraft, saved: AgentDraft): SystemConfigPatchNew {
  const patch: SystemConfigPatchNew = {};
  if (draft.anthropicKey.trim()) patch.anthropic_api_key = draft.anthropicKey.trim();
  if (draft.anthropicBaseUrl !== saved.anthropicBaseUrl)
    patch.anthropic_base_url = draft.anthropicBaseUrl || "";
  if (draft.anthropicModel !== saved.anthropicModel)
    patch.anthropic_model = draft.anthropicModel || "";
  if (draft.haikuModel !== saved.haikuModel)
    patch.anthropic_default_haiku_model = draft.haikuModel || "";
  if (draft.opusModel !== saved.opusModel)
    patch.anthropic_default_opus_model = draft.opusModel || "";
  if (draft.sonnetModel !== saved.sonnetModel)
    patch.anthropic_default_sonnet_model = draft.sonnetModel || "";
  if (draft.subagentModel !== saved.subagentModel)
    patch.claude_code_subagent_model = draft.subagentModel || "";
  return patch;
}

// ---------------------------------------------------------------------------
// Shared style constants
// ---------------------------------------------------------------------------

const cardClassName = "rounded-xl border border-gray-800 bg-gray-950/40 p-4";
const inputClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60";
const vendorIconFrameClassName =
  "rounded-2xl border border-gray-800 bg-gray-900 px-3 py-3 shadow-inner shadow-white/5";

// Small inline clear button shown next to "当前：" when a value is set
const inlineClearClassName =
  "ml-1.5 inline-flex items-center rounded p-0.5 text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface AgentConfigTabProps {
  visible: boolean;
}

export function AgentConfigTab({ visible }: AgentConfigTabProps) {
  const [remoteData, setRemoteData] = useState<GetSystemConfigResponseNew | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDraft>({
    anthropicKey: "",
    anthropicBaseUrl: "",
    anthropicModel: "",
    haikuModel: "",
    opusModel: "",
    sonnetModel: "",
    subagentModel: "",
  });
  const savedRef = useRef<AgentDraft>({
    anthropicKey: "",
    anthropicBaseUrl: "",
    anthropicModel: "",
    haikuModel: "",
    opusModel: "",
    sonnetModel: "",
    subagentModel: "",
  });
  const [saving, setSaving] = useState(false);
  const [clearingField, setClearingField] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [modelRoutingExpanded, setModelRoutingExpanded] = useState(false);

  // Load config on mount
  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await API.getSystemConfigNew();
      setRemoteData(res);
      const d = buildDraft(res);
      savedRef.current = d;
      setDraft(d);
    } catch (err) {
      setLoadError((err as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const isDirty = !deepEqual(draft, savedRef.current);

  const updateDraft = useCallback(
    <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => {
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
      const res = await API.updateSystemConfigNew(patch);
      setRemoteData(res);
      const newDraft = buildDraft(res);
      savedRef.current = newDraft;
      setDraft(newDraft);
      useConfigStatusStore.getState().refresh();
      useAppStore.getState().pushToast("ArcReel 智能体配置已保存", "success");
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [draft]);

  const handleReset = useCallback(() => {
    setDraft(savedRef.current);
    setSaveError(null);
  }, []);

  // Clear a single field immediately via PATCH
  const handleClearField = useCallback(
    async (fieldId: string, patch: SystemConfigPatchNew, label: string) => {
      setClearingField(fieldId);
      try {
        const res = await API.updateSystemConfigNew(patch);
        setRemoteData(res);
        const nextSavedDraft = buildDraft(res);
        savedRef.current = nextSavedDraft;
        setDraft(nextSavedDraft);
        useConfigStatusStore.getState().refresh();
        useAppStore.getState().pushToast(`${label} 已清除`, "success");
      } catch (err) {
        useAppStore.getState().pushToast(`清除失败: ${(err as Error).message}`, "error");
      } finally {
        setClearingField(null);
      }
    },
    [],
  );

  const isBusy = saving || clearingField !== null;

  // Loading / error states
  if (loadError) {
    return (
      <div className={visible ? "px-6 py-8" : "hidden"}>
        <div className="text-sm text-rose-400">加载失败: {loadError}</div>
        <button
          type="button"
          onClick={() => void load()}
          className="mt-3 inline-flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300 hover:border-gray-600 hover:bg-gray-800/50"
        >
          <Loader2 className="h-4 w-4" />
          重试
        </button>
      </div>
    );
  }

  if (!remoteData) {
    return (
      <div className={visible ? "flex items-center gap-2 px-6 py-8 text-gray-400" : "hidden"}>
        <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
        加载中…
      </div>
    );
  }

  const settings = remoteData.settings;

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-5 px-6 pb-0 pt-6">
        {/* Anthropic API Key card */}
        <div className={cardClassName}>
          <div className="flex items-start gap-3">
            <div className={vendorIconFrameClassName}>
              <ClaudeColor size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-gray-100">Anthropic API Key</div>
              <div className="mt-1 text-xs text-gray-400">
                驱动 ArcReel 智能体的核心 AI 能力。
              </div>
              {/* 当前值行 */}
              <div className="mt-1 flex items-center text-xs text-gray-500">
                <span className="truncate">
                  当前：{settings.anthropic_api_key.masked ?? "未设置"}
                </span>
                {settings.anthropic_api_key.is_set && (
                  <button
                    type="button"
                    onClick={() =>
                      void handleClearField(
                        "anthropic_api_key",
                        { anthropic_api_key: "" },
                        "Anthropic API Key",
                      )
                    }
                    disabled={isBusy}
                    className={inlineClearClassName}
                    aria-label="清除已保存的 Anthropic API Key"
                  >
                    {clearingField === "anthropic_api_key" ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <X className="h-3 w-3" />
                    )}
                  </button>
                )}
              </div>
            </div>
          </div>
          {/* Key input */}
          <div className="relative mt-3">
            <input
              type={showKey ? "text" : "password"}
              value={draft.anthropicKey}
              onChange={(e) => updateDraft("anthropicKey", e.target.value)}
              placeholder="sk-ant-…"
              className={`${inputClassName} pr-10`}
              autoComplete="off"
              spellCheck={false}
              name="anthropic_api_key"
              aria-label="Anthropic API Key"
              disabled={saving}
            />
            {draft.anthropicKey && (
              <button
                type="button"
                onClick={() => updateDraft("anthropicKey", "")}
                className="absolute right-8 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                aria-label="清除输入"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
              aria-label={showKey ? "隐藏密钥" : "显示密钥"}
            >
              {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>

          {/* Base URL */}
          <div className="mt-4 border-t border-gray-800 pt-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-gray-100">Base URL</div>
              {settings.anthropic_base_url && (
                <button
                  type="button"
                  onClick={() =>
                    void handleClearField(
                      "anthropic_base_url",
                      { anthropic_base_url: "" },
                      "Anthropic Base URL",
                    )
                  }
                  disabled={isBusy}
                  className="inline-flex items-center gap-1 text-xs text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label="清除已保存的 Anthropic Base URL"
                >
                  {clearingField === "anthropic_base_url" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                  清除已保存
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-gray-400">
              可选。留空使用官方默认地址，使用代理时填写网关地址。
            </div>
            <div className="relative mt-3">
              <input
                value={draft.anthropicBaseUrl}
                onChange={(e) => updateDraft("anthropicBaseUrl", e.target.value)}
                placeholder="https://anthropic-proxy.example.com"
                className={`${inputClassName}${draft.anthropicBaseUrl ? " pr-8" : ""}`}
                autoComplete="off"
                spellCheck={false}
                name="anthropic_base_url"
                aria-label="Anthropic Base URL"
                disabled={saving}
              />
              {draft.anthropicBaseUrl && (
                <button
                  type="button"
                  onClick={() => updateDraft("anthropicBaseUrl", "")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                  aria-label="清除 Anthropic Base URL 输入"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Model */}
          <div className="mt-4 border-t border-gray-800 pt-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-gray-100">模型配置</div>
              {settings.anthropic_model && (
                <button
                  type="button"
                  onClick={() =>
                    void handleClearField(
                      "anthropic_model",
                      { anthropic_model: "" },
                      "ANTHROPIC_MODEL",
                    )
                  }
                  disabled={isBusy}
                  className="inline-flex items-center gap-1 text-xs text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label="清除已保存的模型配置"
                >
                  {clearingField === "anthropic_model" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                  清除已保存
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-gray-400">
              可选。覆盖 Claude Agent SDK 的默认模型，留空使用 SDK 默认值。
            </div>
            <div className="relative mt-3">
              <input
                value={draft.anthropicModel}
                onChange={(e) => updateDraft("anthropicModel", e.target.value)}
                placeholder="ANTHROPIC_MODEL"
                className={`${inputClassName}${draft.anthropicModel ? " pr-8" : ""}`}
                autoComplete="off"
                spellCheck={false}
                name="anthropic_model"
                aria-label="ANTHROPIC_MODEL"
                disabled={saving}
              />
              {draft.anthropicModel && (
                <button
                  type="button"
                  onClick={() => updateDraft("anthropicModel", "")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                  aria-label="清除模型配置输入"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Advanced model routing */}
            <details
              open={modelRoutingExpanded}
              onToggle={(e) => setModelRoutingExpanded(e.currentTarget.open)}
              className="mt-4 rounded-xl border border-gray-800 bg-gray-950/40 p-4"
            >
              <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-gray-100">
                <span className="inline-flex items-center gap-2">
                  <SlidersHorizontal className="h-4 w-4 text-gray-400" />
                  高级模型配置
                </span>
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-gray-800 bg-gray-900 text-gray-500">
                  <ChevronDown
                    className={`h-4 w-4 transition-transform duration-200 ${
                      modelRoutingExpanded ? "rotate-180 text-gray-200" : ""
                    }`}
                  />
                </span>
              </summary>
              <div className="mt-4 grid gap-4">
                {(
                  [
                    {
                      key: "haikuModel" as const,
                      label: "Haiku 模型",
                      placeholder: "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                      settingsValue: settings.anthropic_default_haiku_model,
                      patchKey: "anthropic_default_haiku_model" as const,
                    },
                    {
                      key: "opusModel" as const,
                      label: "Opus 模型",
                      placeholder: "ANTHROPIC_DEFAULT_OPUS_MODEL",
                      settingsValue: settings.anthropic_default_opus_model,
                      patchKey: "anthropic_default_opus_model" as const,
                    },
                    {
                      key: "sonnetModel" as const,
                      label: "Sonnet 模型",
                      placeholder: "ANTHROPIC_DEFAULT_SONNET_MODEL",
                      settingsValue: settings.anthropic_default_sonnet_model,
                      patchKey: "anthropic_default_sonnet_model" as const,
                    },
                    {
                      key: "subagentModel" as const,
                      label: "子 Agent 模型",
                      placeholder: "CLAUDE_CODE_SUBAGENT_MODEL",
                      settingsValue: settings.claude_code_subagent_model,
                      patchKey: "claude_code_subagent_model" as const,
                    },
                  ] as const
                ).map(({ key, label, placeholder, settingsValue, patchKey }) => (
                  <div key={key} className={cardClassName}>
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-medium text-gray-100">{label}</div>
                      {settingsValue && (
                        <button
                          type="button"
                          onClick={() =>
                            void handleClearField(
                              patchKey,
                              { [patchKey]: "" } as SystemConfigPatchNew,
                              label,
                            )
                          }
                          disabled={isBusy}
                          className="inline-flex items-center gap-1 text-xs text-gray-600 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                          aria-label={`清除已保存的 ${label}`}
                        >
                          {clearingField === patchKey ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <X className="h-3 w-3" />
                          )}
                          清除已保存
                        </button>
                      )}
                    </div>
                    <div className="relative mt-2">
                      <input
                        value={draft[key]}
                        onChange={(e) => updateDraft(key, e.target.value)}
                        placeholder={placeholder}
                        className={`${inputClassName}${draft[key] ? " pr-8" : ""}`}
                        autoComplete="off"
                        spellCheck={false}
                        disabled={saving}
                      />
                      {draft[key] && (
                        <button
                          type="button"
                          onClick={() => updateDraft(key, "")}
                          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
                          aria-label={`清除 ${label} 输入`}
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-3 text-xs text-gray-500">
                分别覆盖按能力等级路由的模型。留空使用 ANTHROPIC_MODEL 或 SDK 默认值。
              </div>
            </details>
          </div>
        </div>
      </div>

      <TabSaveFooter
        isDirty={isDirty}
        saving={saving}
        disabled={clearingField !== null}
        error={saveError}
        onSave={() => void handleSave()}
        onReset={handleReset}
      />
    </div>
  );
}
