import { useEffect, useMemo } from "react";
import { useSearch } from "wouter";
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Brain,
  Cable,
  Film,
  Info,
  KeyRound,
  Plug,
  ScrollText,
  SlidersHorizontal,
  Store,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { SETTINGS_SECTIONS, settingsSectionPath, type SettingsSection } from "@/app-routes";
import { AgentMemoryCabinet } from "@/components/agent/AgentMemoryCabinet";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { PageShell, type ContainerTier } from "@/components/shared/page-shell/PageShell";
import { PageSidebar, type PageSidebarGroup } from "@/components/shared/page-shell/PageSidebar";
import { useReturnTo } from "@/components/shared/page-shell/return-to";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { useConfigStatusStore, useSectionConfigIssues } from "@/stores/config-status-store";
import { UsageRecordsSection } from "../usage/UsageRecordsSection";
import { AgentConfigTab } from "./AgentConfigTab";
import { ProviderSection } from "./ProviderSection";
import { AboutSection } from "./settings/AboutSection";
import { AccessTokensSection } from "./settings/agent-access/AccessTokensSection";
import { ExternalAgentSection } from "./settings/agent-access/ExternalAgentSection";
import { ConfigIssueNotice } from "./settings/ConfigIssueNotice";
import { EndpointsSection } from "./settings/endpoints/EndpointsSection";
import { GeneralSection } from "./settings/GeneralSection";
import { MarketSection } from "./settings/market/MarketSection";
import { MediaModelSection } from "./settings/MediaModelSection";
import { PromptTemplatesSection } from "./settings/PromptTemplatesSection";

interface SectionDef {
  labelKey: string;
  icon: LucideIcon;
  tier: ContainerTier;
}

const SECTIONS: Record<SettingsSection, SectionDef> = {
  providers: { labelKey: "dashboard:providers", icon: Plug, tier: "bleed" },
  "default-models": { labelKey: "dashboard:settings_default_models", icon: Film, tier: "constrained" },
  endpoints: { labelKey: "dashboard:ce_section_title", icon: Waypoints, tier: "bleed" },
  "arcreel-agent": { labelKey: "dashboard:settings_arcreel_agent", icon: Bot, tier: "constrained" },
  "agent-memory": { labelKey: "dashboard:settings_agent_memory", icon: Brain, tier: "bleed" },
  "external-agent": { labelKey: "dashboard:settings_external_agent", icon: Cable, tier: "constrained" },
  "access-tokens": { labelKey: "dashboard:settings_access_tokens", icon: KeyRound, tier: "constrained" },
  market: { labelKey: "dashboard:market_section_title", icon: Store, tier: "full" },
  usage: { labelKey: "dashboard:usage", icon: BarChart3, tier: "full" },
  general: { labelKey: "dashboard:settings_general", icon: SlidersHorizontal, tier: "constrained" },
  "prompt-templates": { labelKey: "dashboard:prompt_templates", icon: ScrollText, tier: "constrained" },
  about: { labelKey: "dashboard:about", icon: Info, tier: "constrained" },
};

/** 侧栏分组与顺序。市场与使用记录各自单独成项，不设分组标题。 */
const SECTION_GROUPS: { id: string; labelKey?: string; sections: SettingsSection[] }[] = [
  { id: "generation", labelKey: "dashboard:settings_group_generation", sections: ["providers", "default-models", "endpoints"] },
  {
    id: "agent",
    labelKey: "dashboard:settings_group_agent",
    sections: ["arcreel-agent", "agent-memory", "external-agent", "access-tokens"],
  },
  { id: "standalone", sections: ["market", "usage"] },
  { id: "system", labelKey: "dashboard:settings_group_system", sections: ["general", "prompt-templates", "about"] },
];

/** 引导第 4、5 步指向的侧栏入口。 */
const SECTION_ONBOARDING_ANCHORS: Partial<Record<SettingsSection, string>> = {
  providers: ONBOARDING_ANCHORS.settingsProviders,
  "arcreel-agent": ONBOARDING_ANCHORS.settingsAgent,
};

function parseSection(search: string): SettingsSection {
  const value = new URLSearchParams(search).get("section");
  return SETTINGS_SECTIONS.find((section) => section === value) ?? "providers";
}

export function SystemConfigPage() {
  const { t } = useTranslation(["common", "dashboard"]);
  const search = useSearch();
  const activeSection = parseSection(search);
  const goBack = useReturnTo();

  const configIssues = useSectionConfigIssues();
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);

  useEffect(() => {
    void fetchConfigStatus();
  }, [fetchConfigStatus]);

  // 侧栏警告点与就地提示都只落在问题所属的分区。
  const issueSections = useMemo(() => new Set(configIssues.map((issue) => issue.section)), [configIssues]);
  const activeIssues = useMemo(
    () => configIssues.filter((issue) => issue.section === activeSection),
    [configIssues, activeSection],
  );
  const groups = useMemo<PageSidebarGroup[]>(
    () =>
      SECTION_GROUPS.map((group) => ({
        id: group.id,
        label: group.labelKey ? t(group.labelKey) : undefined,
        items: group.sections.map((id) => ({
          id,
          label: t(SECTIONS[id].labelKey),
          icon: SECTIONS[id].icon,
          href: settingsSectionPath(id),
          onboardingAnchor: SECTION_ONBOARDING_ANCHORS[id],
          badge:
            issueSections.has(id) ? (
              <span role="img" aria-label={t("dashboard:config_incomplete")} className="text-warn">
                <AlertTriangle aria-hidden className="size-3.5" />
              </span>
            ) : undefined,
        })),
      })),
    [t, issueSections],
  );

  return (
    <PageShell
      header={<PageHeader back={{ label: t("common:back"), onClick: goBack }} title={t("common:settings")} />}
      sidebar={<PageSidebar label={t("common:settings")} groups={groups} activeId={activeSection} replace />}
      tier={SECTIONS[activeSection].tier}
    >
      {/* 全出血分区没有统一的页头，提示放在分区顶部；限宽分区在自己的页头说明之后放提示 */}
      {SECTIONS[activeSection].tier === "bleed" && activeIssues.length > 0 && (
        <div className="shrink-0 px-6 pt-4">
          <ConfigIssueNotice issues={activeIssues} />
        </div>
      )}
      <SectionContent section={activeSection} />
    </PageShell>
  );
}

function SectionContent({ section }: { section: SettingsSection }) {
  switch (section) {
    case "providers":
      return <ProviderSection />;
    case "default-models":
      return <MediaModelSection />;
    case "endpoints":
      return <EndpointsSection />;
    case "arcreel-agent":
      return <AgentConfigTab />;
    case "agent-memory":
      // 记忆编辑器重做前先整体放进一栏滚动；全出血档不提供内边距。
      return (
        <div className="relative min-h-0 flex-1 overflow-y-auto p-6 [scrollbar-gutter:stable]">
          <AgentMemoryCabinet scope={{ level: "user" }} frame="section" />
        </div>
      );
    case "external-agent":
      return <ExternalAgentSection />;
    case "access-tokens":
      return <AccessTokensSection />;
    case "market":
      return <MarketSection />;
    case "usage":
      return <UsageRecordsSection />;
    case "general":
      return <GeneralSection />;
    case "prompt-templates":
      return <PromptTemplatesSection />;
    case "about":
      return <AboutSection />;
  }
}
