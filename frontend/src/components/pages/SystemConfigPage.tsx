import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { AlertTriangle, Bot, ChevronLeft, Gauge, Image, KeyRound, Loader2 } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponse } from "@/types";
import { ApiKeysTab } from "./ApiKeysTab";
import { AgentConfigTab } from "./AgentConfigTab";
import { AdvancedConfigTab } from "./AdvancedConfigTab";
import { MediaConfigTab } from "./MediaConfigTab";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SettingsTab = "agent" | "media" | "advanced" | "api-keys";

interface TabDirtyState {
  agent: boolean;
  media: boolean;
  advanced: boolean;
}

// ---------------------------------------------------------------------------
// Tab navigation config
// ---------------------------------------------------------------------------

const TAB_LIST: { id: SettingsTab; label: string; icon: React.ReactNode }[] = [
  {
    id: "agent",
    label: "ArcReel 智能体配置",
    icon: <Bot className="h-3.5 w-3.5" />,
  },
  {
    id: "media",
    label: "AI 生图/生视频配置",
    icon: <Image className="h-3.5 w-3.5" />,
  },
  {
    id: "advanced",
    label: "高级配置",
    icon: <Gauge className="h-3.5 w-3.5" />,
  },
  {
    id: "api-keys",
    label: "API Keys",
    icon: <KeyRound className="h-3.5 w-3.5" />,
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
  const [, navigate] = useLocation();
  const search = useSearch();
  const initialTab = useMemo<SettingsTab>(() => {
    const params = new URLSearchParams(search);
    const tab = params.get("tab");
    if (tab === "api-keys") return "api-keys";
    if (tab === "media") return "media";
    if (tab === "advanced") return "advanced";
    return "agent";
  }, [search]);

  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const [data, setData] = useState<GetSystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tabDirty, setTabDirty] = useState<TabDirtyState>({
    agent: false,
    media: false,
    advanced: false,
  });

  const configIssues = useConfigStatusStore((s) => s.issues);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);

  const makeOnDirtyChange = (tab: keyof TabDirtyState) => (dirty: boolean) => {
    setTabDirty((prev) => (prev[tab] === dirty ? prev : { ...prev, [tab]: dirty }));
  };

  // Stable callbacks (won't change on re-render)
  const onAgentDirty = useRef(makeOnDirtyChange("agent")).current;
  const onMediaDirty = useRef(makeOnDirtyChange("media")).current;
  const onAdvancedDirty = useRef(makeOnDirtyChange("advanced")).current;

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await API.getSystemConfig();
      setData(res);
    } catch (err) {
      const message = (err as Error).message;
      setLoadError(message);
      useAppStore.getState().pushToast(`加载失败: ${message}`, "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void fetchConfigStatus();
  }, [load, fetchConfigStatus]);

  const handleSaved = useCallback((updated: GetSystemConfigResponse) => {
    setData(updated);
  }, []);

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

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
            <Loader2 className="h-5 w-5 animate-spin text-indigo-400" aria-hidden="true" />
            加载配置中…
          </div>
        </main>
      </div>
    );
  }

  if (!data) {
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
          <div className="rounded-2xl border border-gray-800 bg-gray-900/90 p-6 shadow-xl shadow-black/20">
            <div className="text-sm font-medium text-rose-200">配置加载失败</div>
            <p className="mt-2 text-sm text-gray-300">
              {loadError ?? "无法获取系统配置，请稍后重试。"}
            </p>
            <div className="mt-5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => void load()}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Loader2 className="h-4 w-4" />
                重试加载
              </button>
              <button
                type="button"
                onClick={() => navigate("/app/projects")}
                className="inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 transition-colors hover:border-gray-600 hover:bg-gray-800/80"
              >
                返回项目页
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Main render
  // -------------------------------------------------------------------------


  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 pt-4">
        <div className="mx-auto max-w-5xl">
          <div className="flex items-center justify-between gap-4 pb-4">
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
                <h1 className="text-lg font-semibold text-gray-100">设置</h1>
                <p className="text-xs text-gray-500">系统配置与 API 访问管理</p>
              </div>
            </div>
          </div>

          {/* Tab 栏 */}
          <div className="flex gap-1 overflow-x-auto" role="tablist">
            {TAB_LIST.map(({ id, label, icon }) => {
              const hasDirty = id !== "api-keys" && tabDirty[id as keyof TabDirtyState];
              const isActive = activeTab === id;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActiveTab(id)}
                  className={`-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 pb-3 text-sm transition-colors ${
                    isActive
                      ? "border-indigo-500 text-indigo-400"
                      : "border-transparent text-gray-500 hover:text-gray-300"
                  }`}
                >
                  {icon}
                  {label}
                  {hasDirty && (
                    <span
                      className="ml-0.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-400"
                      aria-label="有未保存变更"
                    />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      </header>

      {/* Config warning banner */}
      {configIssues.length > 0 && (
        <div className="border-b border-amber-900/40 bg-amber-950/30 px-6 py-3">
          <div className="mx-auto flex max-w-5xl items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
            <div className="text-sm text-amber-200">
              <span className="font-medium">以下必填配置尚未完成：</span>
              <ul className="mt-1 space-y-0.5">
                {configIssues.map((issue) => (
                  <li key={issue.key}>
                    <button
                      type="button"
                      onClick={() => setActiveTab(issue.tab)}
                      className="underline underline-offset-2 hover:text-amber-100"
                    >
                      {issue.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Tab content — keep all config tabs mounted to preserve dirty state */}
      <main className="mx-auto max-w-5xl pb-16">
        <AgentConfigTab
          data={data}
          onSaved={handleSaved}
          onDirtyChange={onAgentDirty}
          visible={activeTab === "agent"}
        />
        <MediaConfigTab
          data={data}
          onSaved={handleSaved}
          onDirtyChange={onMediaDirty}
          visible={activeTab === "media"}
        />
        <AdvancedConfigTab
          data={data}
          onSaved={handleSaved}
          onDirtyChange={onAdvancedDirty}
          visible={activeTab === "advanced"}
        />
        {activeTab === "api-keys" && (
          <div className="px-6 py-8">
            <ApiKeysTab />
          </div>
        )}
      </main>
    </div>
  );
}
