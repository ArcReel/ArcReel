import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { AlertTriangle, BarChart3, Bot, ChevronLeft, Film, Loader2, Plug } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponse } from "@/types";
import { ApiKeysTab } from "./ApiKeysTab";
import { AgentConfigTab } from "./AgentConfigTab";
import { AdvancedConfigTab } from "./AdvancedConfigTab";
import { MediaConfigTab } from "./MediaConfigTab";
import { ProviderSection } from "./ProviderSection";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SettingsSection = "agent" | "providers" | "media" | "usage";

interface SectionDirtyState {
  agent: boolean;
  media: boolean;
  advanced: boolean;
}

// ---------------------------------------------------------------------------
// Sidebar navigation config
// ---------------------------------------------------------------------------

const SECTION_LIST: { id: SettingsSection; label: string; icon: React.ReactNode }[] = [
  {
    id: "agent",
    label: "智能体",
    icon: <Bot className="h-4 w-4" />,
  },
  {
    id: "providers",
    label: "供应商",
    icon: <Plug className="h-4 w-4" />,
  },
  {
    id: "media",
    label: "图片/视频",
    icon: <Film className="h-4 w-4" />,
  },
  {
    id: "usage",
    label: "用量统计",
    icon: <BarChart3 className="h-4 w-4" />,
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
  const [location, navigate] = useLocation();
  const search = useSearch();

  const getActiveSection = (): SettingsSection => {
    const params = new URLSearchParams(search);
    const section = params.get("section");
    if (section === "providers") return "providers";
    if (section === "media") return "media";
    if (section === "usage") return "usage";
    return "agent";
  };

  const activeSection = getActiveSection();

  const setActiveSection = (section: SettingsSection) => {
    const params = new URLSearchParams(search);
    params.set("section", section);
    navigate(`${location}?${params.toString()}`, { replace: true });
  };

  const [data, setData] = useState<GetSystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sectionDirty, setSectionDirty] = useState<SectionDirtyState>({
    agent: false,
    media: false,
    advanced: false,
  });

  const configIssues = useConfigStatusStore((s) => s.issues);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);

  const makeOnDirtyChange = (tab: keyof SectionDirtyState) => (dirty: boolean) => {
    setSectionDirty((prev) => (prev[tab] === dirty ? prev : { ...prev, [tab]: dirty }));
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
    <div className="flex h-screen flex-col bg-gray-950 text-gray-100">
      {/* Page header */}
      <header className="shrink-0 border-b border-gray-800 px-6 py-4">
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
      </header>

      {/* Body: sidebar + content */}
      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <nav className="w-48 shrink-0 border-r border-gray-800 bg-gray-950/50 py-4">
          {SECTION_LIST.map(({ id, label, icon }) => {
            const isActive = activeSection === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setActiveSection(id)}
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? "border-l-2 border-indigo-500 bg-gray-800/50 text-white"
                    : "border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200"
                }`}
              >
                {icon}
                {label}
              </button>
            );
          })}
        </nav>

        {/* Content area */}
        <div className="flex-1 overflow-y-auto">
          {/* Config warning banner */}
          {configIssues.length > 0 && (
            <div className="border-b border-amber-900/40 bg-amber-950/30 px-6 py-3">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                <div className="text-sm text-amber-200">
                  <span className="font-medium">以下必填配置尚未完成：</span>
                  <ul className="mt-1 space-y-0.5">
                    {configIssues.map((issue) => (
                      <li key={issue.key}>
                        <button
                          type="button"
                          onClick={() => setActiveSection(issue.tab)}
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

          {/* Section content */}
          {activeSection === "agent" && (
            <AgentConfigTab
              data={data}
              onSaved={handleSaved}
              onDirtyChange={onAgentDirty}
              visible={true}
            />
          )}
          {activeSection === "providers" && <ProviderSection />}
          {activeSection === "media" && (
            <MediaConfigTab
              data={data}
              onSaved={handleSaved}
              onDirtyChange={onMediaDirty}
              visible={true}
            />
          )}
          {activeSection === "usage" && (
            <div className="px-6 py-8 text-gray-400">用量统计 (placeholder)</div>
          )}
        </div>
      </div>
    </div>
  );
}
