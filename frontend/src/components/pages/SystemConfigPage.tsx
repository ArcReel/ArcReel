
import { useEffect, useMemo } from "react";
import { useLocation, useSearch } from "wouter";
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Brain,
  Cable,
  Film,
  Info,
  KeyRound,
  Languages,
  Plug,
  ScrollText,
  Store,
  Waypoints,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ProtoPageShell, type ShellNavGroup } from "@/prototype/page-shell/ProtoPageShell";
import { TIER_NAMES, useShellParams, type Tier } from "@/prototype/page-shell/shell";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { AgentConfigTab } from "./AgentConfigTab";
import { ApiKeysTab } from "./ApiKeysTab";
import { AboutSection } from "./settings/AboutSection";
import { MediaModelSection } from "./settings/MediaModelSection";
import { PromptTemplatesSection } from "./settings/PromptTemplatesSection";
import { ProviderSection } from "./ProviderSection";
import { UsageRecordsSection } from "../usage/UsageRecordsSection";
import { EndpointsSection } from "./settings/endpoints/EndpointsSection";
import { MarketSection } from "./settings/market/MarketSection";
import {
  SUPPORTED_LANGUAGES,
  LANGUAGE_DISPLAY_LABELS,
  type SupportedLanguage,
} from "@/i18n";

// 全局设置页 · "Control Booth"
// 延续 Darkroom 美学：editorial 大标题 + mono kicker + 分组侧栏 + accent 紫色高亮。

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SettingsSection =
  | "agent"
  | "providers"
  | "endpoints"
  | "market"
  | "media"
  | "usage"
  | "api-keys"
  | "prompt-templates"
  | "about"
  | "agent-memory"
  | "external-agent"
  | "general";

/** 各分区的容器档位（原型提案）。 */
const SECTION_TIER: Record<SettingsSection, Tier> = {
  providers: "bleed",
  media: "form",
  endpoints: "bleed",
  agent: "form",
  "agent-memory": "bleed",
  "external-agent": "form",
  "api-keys": "form",
  market: "wide",
  usage: "wide",
  general: "form",
  "prompt-templates": "form",
  about: "reading",
};

/** 引导第 5/6 步指向的侧栏入口——只有这两项挂锚点，其余小节不在当前引导覆盖范围内。 */
const SECTION_ONBOARDING_ANCHORS: Partial<Record<SettingsSection, string>> = {
  providers: ONBOARDING_ANCHORS.settingsProviders,
  agent: ONBOARDING_ANCHORS.settingsAgent,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfigPage() {
  const { t, i18n } = useTranslation(["common", "dashboard"]);
  const [location, navigate] = useLocation();
  const search = useSearch();

  const activeSection = useMemo((): SettingsSection => {
    const section = new URLSearchParams(search).get("section");
    if (section === "agent") return "agent";
    if (section === "endpoints") return "endpoints";
    if (section === "market") return "market";
    if (section === "media") return "media";
    if (section === "usage") return "usage";
    if (section === "api-keys") return "api-keys";
    if (section === "prompt-templates") return "prompt-templates";
    if (section === "about") return "about";
    if (section === "agent-memory") return "agent-memory";
    if (section === "external-agent") return "external-agent";
    if (section === "general") return "general";
    return "providers";
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

  const currentLang = i18n.language.split("-")[0] as SupportedLanguage;
  const langDisplay = LANGUAGE_DISPLAY_LABELS[currentLang] ?? i18n.language;
  const cycleLang = () => {
    const idx = SUPPORTED_LANGUAGES.indexOf(currentLang);
    const nextIdx = idx === -1 ? 0 : (idx + 1) % SUPPORTED_LANGUAGES.length;
    void i18n.changeLanguage(SUPPORTED_LANGUAGES[nextIdx]);
  };

  const { variant, guides } = useShellParams();
  const issueBadge = configIssues.length > 0 && (
    <AlertTriangle aria-label={t("dashboard:config_incomplete")} className="h-3.5 w-3.5 shrink-0 text-warm" />
  );

  // 侧栏按「全局设置的信息架构」（#2968）的分组与顺序；斜体项是原型占位。
  const groups: ShellNavGroup[] = [
    {
      label: "生成",
      items: [
        { id: "providers", label: "供应商", Icon: Plug, badge: issueBadge, onboardingAnchor: SECTION_ONBOARDING_ANCHORS.providers },
        { id: "media", label: "默认模型", Icon: Film, badge: issueBadge },
        { id: "endpoints", label: "调用端点", Icon: Waypoints },
      ],
    },
    {
      label: "Agent",
      items: [
        { id: "agent", label: "ArcReel Agent", Icon: Bot, badge: issueBadge, onboardingAnchor: SECTION_ONBOARDING_ANCHORS.agent },
        { id: "agent-memory", label: "Agent 记忆", Icon: Brain, placeholder: true },
        { id: "external-agent", label: "外部 Agent 接入", Icon: Cable, placeholder: true },
        { id: "api-keys", label: "访问令牌", Icon: KeyRound },
      ],
    },
    {
      items: [
        { id: "market", label: "市场", Icon: Store },
        { id: "usage", label: "使用记录", Icon: BarChart3 },
      ],
    },
    {
      label: "系统",
      items: [
        { id: "general", label: "通用", Icon: Languages, placeholder: true },
        { id: "prompt-templates", label: "提示词模版", Icon: ScrollText },
        { id: "about", label: "关于", Icon: Info },
      ],
    },
  ];
  const activeLabel = groups.flatMap((g) => g.items).find((i) => i.id === activeSection)?.label ?? "";

  return (
    <ProtoPageShell
      variant={variant}
      guides={guides}
      title={t("common:settings")}
      back={{ label: t("common:back"), onClick: () => navigate("/app/projects") }}
      groups={groups}
      navLabel={t("common:settings")}
      activeId={activeSection}
      onSelect={(id) => setActiveSection(id as SettingsSection)}
      tier={SECTION_TIER[activeSection]}
      viewTitle={activeLabel}
    >
      {activeSection === "providers" && <ProviderSection />}
      {activeSection === "endpoints" && <EndpointsSection />}
      {activeSection === "market" && <MarketSection />}
      {activeSection === "agent" && <AgentConfigTab visible />}
      {activeSection === "media" && <MediaModelSection />}
      {activeSection === "usage" && <UsageRecordsSection />}
      {activeSection === "api-keys" && <ApiKeysTab />}
      {activeSection === "prompt-templates" && <PromptTemplatesSection />}
      {activeSection === "about" && <AboutSection />}
      {activeSection === "agent-memory" && (
        <ProtoPlaceholder tier="bleed" text="Agent 记忆：多文件编辑器，全出血档（文件列表 + 编辑区，每个文件一个保存单元）。" />
      )}
      {activeSection === "external-agent" && (
        <ProtoPlaceholder tier="form" text="外部 Agent 接入：原 ExternalAgentModal 的内容改为独立分区，表单档。" />
      )}
      {activeSection === "general" && (
        <div className="space-y-4">
          <ProtoPlaceholder tier="form" text="通用：界面语言、重看引导。表单档。" />
          <button type="button" onClick={cycleLang} className="rounded-md border border-border px-3 py-1.5 text-[13px] hover:bg-accent">
            界面语言：{langDisplay}（点击切换）
          </button>
        </div>
      )}
    </ProtoPageShell>
  );
}

function ProtoPlaceholder({ tier, text }: { tier: Tier; text: string }) {
  return (
    <div
      className={
        "rounded-lg border border-dashed border-border p-6 text-[13px] leading-[1.6] text-text-3 " +
        (tier === "bleed" ? "m-6 flex-1" : "")
      }
    >
      原型占位（{TIER_NAMES[tier]}）—— {text}
    </div>
  );
}
