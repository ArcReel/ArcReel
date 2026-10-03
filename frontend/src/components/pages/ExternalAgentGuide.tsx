import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, KeyRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";
import { settingsSectionPath } from "@/app-routes";
import { copyText } from "@/utils/clipboard";

const MCP_ENDPOINT = `${window.location.origin}/mcp`;
const INSTALL_COMMAND = "npx skills add ArcReel/skills";
const SETUP_SKILL = "setup-arcreel-skills";
const INSTALL_GUIDE_URL = `${window.location.origin}/agent-installation-guide.md`;

type InstallTab = "manual" | "agent";
type CopyTarget = "mcp_endpoint" | "install" | "setup" | "prompt";

/** 外部 Agent 接入指引：准备访问令牌，再选择让 AI Agent 自行接入或手动安装。弹窗与全局设置的「外部 Agent 接入」分区共用。 */
export function ExternalAgentGuide({ className }: { className?: string }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [activeTab, setActiveTab] = useState<InstallTab>("agent");
  const [copied, setCopied] = useState<CopyTarget | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const manualTabRef = useRef<HTMLButtonElement>(null);
  const agentTabRef = useRef<HTMLButtonElement>(null);
  const copiedTimerRef = useRef<number | null>(null);
  const agentPrompt = t("dashboard:external_agent_prompt", { guideUrl: INSTALL_GUIDE_URL });

  const handleCopy = useCallback((target: CopyTarget, value: string) => {
    void copyText(value).then(() => {
      setCopyFailed(false);
      setCopied(target);
      if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = window.setTimeout(() => {
        copiedTimerRef.current = null;
        setCopied(null);
      }, 2000);
    }, () => {
      setCopied(null);
      setCopyFailed(true);
    });
  }, []);

  useEffect(
    () => () => {
      if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
    },
    [],
  );

  const selectTab = (tab: InstallTab) => {
    setActiveTab(tab);
    (tab === "manual" ? manualTabRef : agentTabRef).current?.focus();
  };

  const copiedMessage = copied ? t(`dashboard:external_agent_${copied}_copied`) : "";

  return (
    <div className={cn("space-y-4", className)}>
      <span role="status" aria-live="polite" className="sr-only">
        {copiedMessage}
      </span>
      {copyFailed && (
        <p
          role="alert"
          className="rounded-lg border border-warn/30 bg-warn/4 p-3 text-[11.5px] text-warn"
        >
          {t("dashboard:external_agent_copy_failed")}
        </p>
      )}

      <section className="rounded-xl border border-primary/25 bg-primary/6 p-4">
        <div className="flex items-start gap-3">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-primary/25 bg-card/60 text-primary">
            <KeyRound className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-[12px] font-semibold text-subtle-foreground">
              {t("dashboard:external_agent_api_key_title")}
            </h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("dashboard:external_agent_api_key_desc")}
            </p>
            <a
              href={settingsSectionPath("access-tokens")}
              target="_blank"
              rel="noopener noreferrer"
              className={`${ACCENT_BTN_CLS} mt-3`}
              style={ACCENT_BUTTON_STYLE}
            >
              {t("dashboard:external_agent_manage_api_keys")}
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </a>
          </div>
        </div>
      </section>

      <div
        role="tablist"
        aria-label={t("dashboard:external_agent_install_method")}
        className="grid grid-cols-2 rounded-lg border border-border bg-background p-1"
      >
        {(["manual", "agent"] as const).map((tab) => (
          <button
            key={tab}
            ref={tab === "manual" ? manualTabRef : agentTabRef}
            type="button"
            role="tab"
            id={`external-agent-${tab}-tab`}
            aria-selected={activeTab === tab}
            aria-controls={`external-agent-${tab}-panel`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => setActiveTab(tab)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                selectTab(activeTab === "agent" ? "manual" : "agent");
              }
            }}
            className={`rounded-md px-3 py-2 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
              activeTab === tab
                ? "bg-primary/12 text-primary shadow-[inset_0_0_0_1px_oklch(0.76_0.09_295_/_0.28)]"
                : "text-muted-foreground hover:text-subtle-foreground"
            }`}
          >
            {t(`dashboard:external_agent_tab_${tab}`)}
          </button>
        ))}
      </div>

      {activeTab === "agent" ? (
        <section
          role="tabpanel"
          id="external-agent-agent-panel"
          aria-labelledby="external-agent-agent-tab"
          className="rounded-xl border border-border/50 bg-card/40 p-4"
        >
          <h3 className="text-[12px] font-semibold text-subtle-foreground">
            {t("dashboard:external_agent_prompt_title")}
          </h3>
          <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
            {t("dashboard:external_agent_prompt_desc")}
          </p>
          <div className="mt-3 rounded-lg border border-border bg-background p-3">
            <code
              translate="no"
              className="block whitespace-pre-wrap break-all text-[11.5px] leading-relaxed text-primary"
            >
              {agentPrompt}
            </code>
          </div>
          <button
            type="button"
            onClick={() => handleCopy("prompt", agentPrompt)}
            className={`${ACCENT_BTN_CLS} mt-3`}
            style={ACCENT_BUTTON_STYLE}
            aria-label={t("dashboard:external_agent_copy_prompt")}
          >
            {copied === "prompt" ? (
              <Check className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Copy className="h-3.5 w-3.5" aria-hidden />
            )}
            {copied === "prompt" ? t("common:copied") : t("dashboard:external_agent_copy_prompt")}
          </button>
        </section>
      ) : (
        <section
          role="tabpanel"
          id="external-agent-manual-panel"
          aria-labelledby="external-agent-manual-tab"
          className="space-y-4 rounded-xl border border-border/50 bg-card/40 p-4"
        >
          <div>
            <h3 className="text-[12px] font-semibold text-subtle-foreground">
              {t("dashboard:external_agent_install_command")}
            </h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("dashboard:external_agent_install_command_desc")}
            </p>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-border bg-background p-2.5">
              <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-primary">
                {INSTALL_COMMAND}
              </code>
              <button
                type="button"
                onClick={() => handleCopy("install", INSTALL_COMMAND)}
                className={GHOST_BTN_CLS}
                aria-label={t("dashboard:external_agent_copy_install_command")}
              >
                {copied === "install" ? (
                  <Check className="h-3 w-3 text-good" aria-hidden />
                ) : (
                  <Copy className="h-3 w-3" aria-hidden />
                )}
                {copied === "install" ? t("common:copied") : t("common:copy")}
              </button>
            </div>
          </div>

          <div>
            <h3 className="text-[12px] font-semibold text-subtle-foreground">
              {t("dashboard:external_agent_setup_command")}
            </h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("dashboard:external_agent_setup_command_desc")}
            </p>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-border bg-background p-2.5">
              <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-primary">
                {SETUP_SKILL}
              </code>
              <button
                type="button"
                onClick={() => handleCopy("setup", SETUP_SKILL)}
                className={GHOST_BTN_CLS}
                aria-label={t("dashboard:external_agent_copy_setup_command")}
              >
                {copied === "setup" ? (
                  <Check className="h-3 w-3 text-good" aria-hidden />
                ) : (
                  <Copy className="h-3 w-3" aria-hidden />
                )}
                {copied === "setup" ? t("common:copied") : t("common:copy")}
              </button>
            </div>
          </div>

          <div>
            <h3 className="text-[12px] font-semibold text-subtle-foreground">
              {t("dashboard:external_agent_mcp_endpoint")}
            </h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("dashboard:external_agent_mcp_endpoint_desc")}
            </p>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-border bg-background p-2.5">
              <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-primary">
                {MCP_ENDPOINT}
              </code>
              <button
                type="button"
                onClick={() => handleCopy("mcp_endpoint", MCP_ENDPOINT)}
                className={GHOST_BTN_CLS}
                aria-label={t("dashboard:external_agent_copy_mcp_endpoint")}
              >
                {copied === "mcp_endpoint" ? (
                  <Check className="h-3 w-3 text-good" aria-hidden />
                ) : (
                  <Copy className="h-3 w-3" aria-hidden />
                )}
                {copied === "mcp_endpoint" ? t("common:copied") : t("common:copy")}
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
