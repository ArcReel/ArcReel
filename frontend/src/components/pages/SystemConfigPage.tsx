
import { useEffect, useMemo } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { AlertTriangle, BarChart3, Bot, ChevronLeft, Film, Info, KeyRound, Languages, Plug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { AgentConfigTab } from "./AgentConfigTab";
import { ApiKeysTab } from "./ApiKeysTab";
import { AboutSection } from "./settings/AboutSection";
import { MediaModelSection } from "./settings/MediaModelSection";
import { ProviderSection } from "./ProviderSection";
import { UsageStatsSection } from "./settings/UsageStatsSection";
import { SUPPORTED_LANGUAGES, LANGUAGE_DISPLAY_LABELS, type SupportedLanguage } from "@/i18n";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SettingsSection = "agent" | "providers" | "media" | "usage" | "api-keys" | "about";

// ---------------------------------------------------------------------------
// Sidebar navigation config
// ---------------------------------------------------------------------------

const SECTION_LIST: { id: SettingsSection; labelKey: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "agent", labelKey: "dashboard:agents", Icon: Bot },
  { id: "providers", labelKey: "dashboard:providers", Icon: Plug },
  { id: "media", labelKey: "dashboard:models", Icon: Film },
  { id: "usage", labelKey: "dashboard:usage", Icon: BarChart3 },
  { id: "api-keys", labelKey: "dashboard:api_keys", Icon: KeyRound },
  { id: "about", labelKey: "dashboard:about", Icon: Info },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
  const { t, i18n } = useTranslation(["common", "dashboard"]);
  const [location, navigate] = useLocation();
  const search = useSearch();

  const activeSection = useMemo((): SettingsSection => {
    const section = new URLSearchParams(search).get("section");
    if (section === "providers") return "providers";
    if (section === "media") return "media";
    if (section === "usage") return "usage";
    if (section === "api-keys") return "api-keys";
    if (section === "about") return "about";
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
            className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-200 hover:border-gray-700 hover:bg-gray-800 focus-ring"
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
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-sm transition-colors focus-ring focus-visible:ring-inset ${
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

          {/* Language toggle */}
          <div className="my-3 mx-4 border-t border-gray-800" />
          <button
            type="button"
            onClick={() => {
              const current = i18n.language.split("-")[0] as SupportedLanguage;
              const idx = SUPPORTED_LANGUAGES.indexOf(current);
              const nextIdx = idx === -1 ? 0 : (idx + 1) % SUPPORTED_LANGUAGES.length;
              void i18n.changeLanguage(SUPPORTED_LANGUAGES[nextIdx]);
            }}
            className="flex w-full items-center gap-3 px-4 py-2.5 text-sm border-l-2 border-transparent text-gray-400 hover:bg-gray-800/30 hover:text-gray-200 transition-colors focus-ring focus-visible:ring-inset"
            title={LANGUAGE_DISPLAY_LABELS[(i18n.language.split("-")[0] as SupportedLanguage)] ?? i18n.language}
          >
            <Languages className="h-4 w-4" />
            <span className="flex-1 text-left">{t("dashboard:language_setting")}</span>
            <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] font-bold uppercase text-gray-400">
              {i18n.language.split("-")[0]}
            </span>
          </button>
        </nav>

        {/* Content area — main is the scroll container.
            providers section bypasses the centered padded wrapper so its sticky bottom bar
            can truly hug the viewport edge (and sidebar can sticky-top across full height). */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          {activeSection === "providers" ? (
            <ProviderSection />
          ) : (
            <div className="mx-auto max-w-4xl px-8 py-8">
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
                        {t(`dashboard:${issue.label}`)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {activeSection === "agent" && <AgentConfigTab visible />}
              {activeSection === "media" && <MediaModelSection />}
              {activeSection === "usage" && <UsageStatsSection />}
              {activeSection === "api-keys" && (
                <div className="p-6">
                  <ApiKeysTab />
                </div>
              )}
              {activeSection === "about" && <AboutSection />}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
