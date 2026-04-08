import { useEffect, useMemo } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { AlertTriangle, BarChart3, Bot, ChevronLeft, Film, KeyRound, Plug } from "lucide-react";
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

const SECTION_LIST: { id: SettingsSection; label: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "agent", label: "Agent", Icon: Bot },
  { id: "providers", label: "Providers", Icon: Plug },
  { id: "media", label: "Model Selection", Icon: Film },
  { id: "usage", label: "Usage Stats", Icon: BarChart3 },
  { id: "api-keys", label: "API Management", Icon: KeyRound },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
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
            aria-label="Back to project lobby"
          >
            <ChevronLeft className="h-4 w-4" />
            Back
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-gray-100">Settings</h1>
            <p className="text-xs text-gray-500">System configuration and API access management</p>
          </div>
        </div>
      </header>

      {/* Body: sidebar + content */}
      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <nav className="w-48 shrink-0 border-r border-gray-800 bg-gray-950/50 py-4">
          {SECTION_LIST.map(({ id, label, Icon }) => {
            const isActive = activeSection === id;
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
                  <span className="font-medium">The following required configurations are incomplete:</span>
                  <ul className="mt-1 space-y-0.5">
                    {configIssues.map((issue) => (
                      <li key={issue.key}>
                        <button
                          type="button"
                          onClick={() => setActiveSection(issue.tab)}
                          className="underline underline-offset-2 hover:text-amber-100 focus-visible:ring-2 focus-visible:ring-amber-400/60 focus-visible:outline-none rounded"
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
          {activeSection === "agent" && <AgentConfigTab visible={true} />}
          {activeSection === "providers" && <ProviderSection />}
          {activeSection === "media" && <MediaModelSection />}
          {activeSection === "usage" && <UsageStatsSection />}
          {activeSection === "api-keys" && (
            <div className="p-6">
              <ApiKeysTab />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
