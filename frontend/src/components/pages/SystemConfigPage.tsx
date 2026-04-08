
import { useEffect, useMemo } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { AlertTriangle, BarChart3, Bot, ChevronLeft, Film, KeyRound, Plug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { AgentConfigTab } from "./AgentConfigTab";
import { ApiKeysTab } from "./ApiKeysTab";
import { MediaModelSection } from "./settings/MediaModelSection";
import { ProviderSection } from "./ProviderSection";
import { UsageStatsSection } from "./settings/UsageStatsSection";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SettingsSection = "agent" | "providers" | "media" | "usage" | "api-keys";

// ---------------------------------------------------------------------------
// Sidebar navigation config
// ---------------------------------------------------------------------------

const SECTION_LIST: { id: SettingsSection; labelKey: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "agent", labelKey: "dashboard:agents", Icon: Bot },
  { id: "providers", labelKey: "dashboard:providers", Icon: Plug },
  { id: "media", labelKey: "dashboard:models", Icon: Film },
  { id: "usage", labelKey: "dashboard:usage", Icon: BarChart3 },
  { id: "api-keys", labelKey: "dashboard:api_keys", Icon: KeyRound },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
  const { t } = useTranslation(["common", "dashboard"]);
  const [location, navigate] = useLocation();
  const search = useSearch();

  const activeSection = useMemo((): SettingsSection => {
    const section = new URLSearchParams(search).get("section");
    if (section === "providers") return "providers";
    if (section === "media") return "media";
    if (section === "usage") return "usage";
    if (section === "api-keys") return "api-keys";
    return "agent";
  }, [search]);

  const setActiveSection = (section: SettingsSection) => {
    const params = new URLSearchParams(search);
    params.set("section", section);
    navigate(`${location}?${params.toString()}`, { replace: true });
  };

  const configIssues = useConfigStatusStore((s) => s.issues);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);

  useEffect(() => {
    void fetchConfigStatus();
  }, [fetchConfigStatus]);

  // -------------------------------------------------------------------------
  // Main render
  // -------------------------------------------------------------------------

  return (
    <div className="flex h-screen flex-col bg-gray-950 text-gray-100">
      {/* Page header */}
      <header className="shrink-0 border-b border-gray-800 px-6 py-4">
        <div className="flex items-center gap-3">
          <Link
            href="/app/projects"
            className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-200 hover:border-gray-700 hover:bg-gray-800 focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:outline-none"
            aria-label={t("common:back")}
          >
            <ChevronLeft className="h-4 w-4" />
            {t("common:back")}
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-gray-100">{t("common:settings")}</h1>
            <p className="text-xs text-gray-500">{t("dashboard:system_config_title")}</p>
          </div>
        </div>
      </header>

      {/* Body: sidebar + content */}
      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <nav className="w-48 shrink-0 border-r border-gray-800 bg-gray-950/50 py-4">
          {SECTION_LIST.map(({ id, labelKey, Icon }) => {
            const isActive = activeSection === id;
            const hasIssue = (id === "providers" || id === "agent" || id === "media") && configIssues.length > 0;

            return (
              <button
                key={id}
                type="button"
                onClick={() => setActiveSection(id)}
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500/60 focus-visible:outline-none ${
                  isActive
                    ? "border-l-2 border-indigo-500 bg-gray-800/50 text-white"
                    : "border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1 text-left">{t(labelKey)}</span>
                {hasIssue && <AlertTriangle className="h-3 w-3 text-rose-500" />}
              </button>
            );
          })}
        </nav>

        {/* Content area */}
        <main className="min-w-0 flex-1 overflow-y-auto px-8 py-8">
          <div className="mx-auto max-w-4xl">
            {/* Quick alert for config issues */}
            {configIssues.length > 0 && (
              <div className="mb-8 rounded-xl border border-rose-500/20 bg-rose-500/5 p-4">
                <div className="flex items-center gap-2 mb-2 text-rose-400">
                  <AlertTriangle className="h-4 w-4" />
                  <h2 className="text-sm font-semibold">{t("dashboard:config_issues")}</h2>
                </div>
                <p className="text-xs text-rose-200/70 mb-3">
                  {t("dashboard:config_issues_hint")}
                </p>
                <ul className="space-y-1.5">
                  {configIssues.map((issue, idx) => (
                    <li key={idx} className="flex items-start gap-2 text-xs text-rose-200/60">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-rose-500/40" />
                      {issue}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {activeSection === "agent" && <AgentConfigTab visible />}
            {activeSection === "providers" && <ProviderSection />}
            {activeSection === "media" && <MediaModelSection />}
            {activeSection === "usage" && <UsageStatsSection />}
            {activeSection === "api-keys" && (
              <div className="p-6">
                <ApiKeysTab />
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
