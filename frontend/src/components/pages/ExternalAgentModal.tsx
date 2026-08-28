import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Check, Copy, ExternalLink, KeyRound, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  DROPDOWN_PANEL_STYLE,
  GHOST_BTN_CLS,
  ICON_BTN_FILLED_CLS,
} from "@/components/ui/darkroom-tokens";
import { ModalShell } from "@/components/ui/ModalShell";
import { copyText } from "@/utils/clipboard";

interface ExternalAgentModalProps {
  onClose: () => void;
}

const MCP_ENDPOINT = `${window.location.origin}/mcp`;
const INSTALL_COMMAND = "npx skills add ArcReel/skills";
const SETUP_COMMAND = "/setup-arcreel-skills";
const INSTALL_GUIDE_URL = `${window.location.origin}/agent-installation-guide.md`;

type InstallTab = "manual" | "agent";
type CopyTarget = "mcp_endpoint" | "install" | "setup" | "prompt";

export function ExternalAgentModal({ onClose }: ExternalAgentModalProps) {
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
    <ModalShell
      open
      onClose={onClose}
      labelledBy="external-agent-modal-title"
      describedBy="external-agent-modal-subtitle"
      className="z-10 flex max-h-[90vh] w-full max-w-lg flex-col overflow-y-auto overscroll-contain rounded-2xl border border-hairline shadow-2xl shadow-black/60"
      style={DROPDOWN_PANEL_STYLE}
    >
      <div
        className="sticky top-0 z-10 flex items-center justify-between border-b border-hairline px-5 py-4"
        style={DROPDOWN_PANEL_STYLE}
      >
        <div className="flex items-center gap-2.5">
          <Bot className="h-5 w-5 text-accent-2" aria-hidden />
          <div>
            <h2 id="external-agent-modal-title" className="text-[14px] font-semibold text-text">
              {t("dashboard:external_agent_guide")}
            </h2>
            <p id="external-agent-modal-subtitle" className="text-[12px] text-text-4">
              {t("dashboard:external_agent_modal_subtitle")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className={ICON_BTN_FILLED_CLS}
          aria-label={t("common:close")}
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <div className="space-y-4 p-5">
        <span role="status" aria-live="polite" className="sr-only">
          {copiedMessage}
        </span>
        {copyFailed && (
          <p
            role="alert"
            className="rounded-lg border border-warm-bright/30 bg-warm-bright/[0.04] p-3 text-[11.5px] text-warm-bright"
          >
            {t("dashboard:external_agent_copy_failed")}
          </p>
        )}

        <section className="rounded-xl border border-accent/25 bg-accent-dim/50 p-4">
          <div className="flex items-start gap-3">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/25 bg-bg-grad-a/60 text-accent-2">
              <KeyRound className="h-4 w-4" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-[12px] font-semibold text-text-2">
                {t("dashboard:external_agent_api_key_title")}
              </h3>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
                {t("dashboard:external_agent_api_key_desc")}
              </p>
              <a
                href="/app/settings?section=api-keys"
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
          className="grid grid-cols-2 rounded-[10px] border border-hairline bg-bg p-1"
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
              className={`rounded-[7px] px-3 py-2 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                activeTab === tab
                  ? "bg-accent-dim text-accent-2 shadow-[inset_0_0_0_1px_oklch(0.76_0.09_295_/_0.28)]"
                  : "text-text-4 hover:text-text-2"
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
            className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4"
          >
            <h3 className="text-[12px] font-semibold text-text-2">
              {t("dashboard:external_agent_prompt_title")}
            </h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
              {t("dashboard:external_agent_prompt_desc")}
            </p>
            <div className="mt-3 rounded-lg border border-hairline bg-bg p-3">
              <code
                translate="no"
                className="block whitespace-pre-wrap break-all text-[11.5px] leading-relaxed text-accent-2"
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
            className="space-y-4 rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4"
          >
            <div>
              <h3 className="text-[12px] font-semibold text-text-2">
                {t("dashboard:external_agent_install_command")}
              </h3>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
                {t("dashboard:external_agent_install_command_desc")}
              </p>
              <div className="mt-2 flex items-center gap-2 rounded-lg border border-hairline bg-bg p-2.5">
                <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-accent-2">
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
              <h3 className="text-[12px] font-semibold text-text-2">
                {t("dashboard:external_agent_setup_command")}
              </h3>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
                {t("dashboard:external_agent_setup_command_desc")}
              </p>
              <div className="mt-2 flex items-center gap-2 rounded-lg border border-hairline bg-bg p-2.5">
                <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-accent-2">
                  {SETUP_COMMAND}
                </code>
                <button
                  type="button"
                  onClick={() => handleCopy("setup", SETUP_COMMAND)}
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
              <h3 className="text-[12px] font-semibold text-text-2">
                {t("dashboard:external_agent_mcp_endpoint")}
              </h3>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
                {t("dashboard:external_agent_mcp_endpoint_desc")}
              </p>
              <div className="mt-2 flex items-center gap-2 rounded-lg border border-hairline bg-bg p-2.5">
                <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-accent-2">
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
    </ModalShell>
  );
}
