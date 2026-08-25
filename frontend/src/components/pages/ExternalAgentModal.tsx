import { useCallback, useEffect, useRef, useState } from "react";
import { Cable, Check, Copy, ExternalLink, KeyRound, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";

import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
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
const INSTALL_COMMANDS = [
  "npx skills add ArcReel/ArcReel@setup-arcreel-skills",
  "npx skills add ArcReel/ArcReel@video-workflow",
].join("\n");
const INSTALL_GUIDE_URL = `${window.location.origin}/agent-installation-guide.md`;

type CopyTarget = "endpoint" | "command";

export function ExternalAgentModal({ onClose }: ExternalAgentModalProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const [copied, setCopied] = useState<CopyTarget | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const copiedTimerRef = useRef<number | null>(null);

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

  const handleGoToApiKeys = useCallback(() => {
    onClose();
    navigate("/app/settings?section=api-keys");
  }, [navigate, onClose]);

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
          <Cable className="h-5 w-5 text-accent-2" aria-hidden />
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

      <div className="space-y-3 p-5">
        <span role="status" aria-live="polite" className="sr-only">
          {copied === "endpoint" && t("dashboard:external_agent_mcp_endpoint_copied")}
          {copied === "command" && t("dashboard:external_agent_install_command_copied")}
        </span>
        {copyFailed && (
          <p
            role="alert"
            className="rounded-lg border border-warm-bright/30 bg-warm-bright/[0.04] p-3 text-[11.5px] text-warm-bright"
          >
            {t("dashboard:external_agent_copy_failed")}
          </p>
        )}
        <section className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4">
          <h3 className="text-[12px] font-semibold text-text-2">
            {t("dashboard:external_agent_mcp_endpoint")}
          </h3>
          <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
            {t("dashboard:external_agent_mcp_endpoint_desc")}
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-hairline bg-bg p-2.5">
            <code translate="no" className="min-w-0 flex-1 break-all text-[11.5px] text-accent-2">
              {MCP_ENDPOINT}
            </code>
            <button
              type="button"
              onClick={() => handleCopy("endpoint", MCP_ENDPOINT)}
              className={GHOST_BTN_CLS}
              aria-label={t("dashboard:external_agent_copy_mcp_endpoint")}
            >
              {copied === "endpoint" ? (
                <Check className="h-3 w-3 text-good" aria-hidden />
              ) : (
                <Copy className="h-3 w-3" aria-hidden />
              )}
              {copied === "endpoint" ? t("common:copied") : t("common:copy")}
            </button>
          </div>
        </section>

        <section className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4">
          <h3 className="text-[12px] font-semibold text-text-2">{t("dashboard:api_key_mgmt")}</h3>
          <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
            {t("dashboard:external_agent_api_key_desc")}
          </p>
          <button
            type="button"
            onClick={handleGoToApiKeys}
            className={`${ACCENT_BTN_CLS} mt-3`}
            style={ACCENT_BUTTON_STYLE}
          >
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
            {t("dashboard:external_agent_manage_api_keys")}
          </button>
        </section>

        <section className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4">
          <h3 className="text-[12px] font-semibold text-text-2">
            {t("dashboard:external_agent_install_command")}
          </h3>
          <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
            {t("dashboard:external_agent_install_command_desc")}
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-hairline bg-bg p-2.5">
            <code
              translate="no"
              className="min-w-0 flex-1 whitespace-pre-wrap break-all text-[11.5px] text-accent-2"
            >
              {INSTALL_COMMANDS}
            </code>
            <button
              type="button"
              onClick={() => handleCopy("command", INSTALL_COMMANDS)}
              className={GHOST_BTN_CLS}
              aria-label={t("dashboard:external_agent_copy_install_command")}
            >
              {copied === "command" ? (
                <Check className="h-3 w-3 text-good" aria-hidden />
              ) : (
                <Copy className="h-3 w-3" aria-hidden />
              )}
              {copied === "command" ? t("common:copied") : t("common:copy")}
            </button>
          </div>
        </section>

        <section className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 p-4" style={CARD_STYLE}>
          <h3 className="text-[12px] font-semibold text-text-2">
            {t("dashboard:external_agent_install_guide")}
          </h3>
          <p className="mt-1 text-[11.5px] leading-[1.55] text-text-4">
            {t("dashboard:external_agent_install_guide_desc")}
          </p>
          <a
            href={INSTALL_GUIDE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={`${GHOST_BTN_CLS} mt-3`}
          >
            {t("dashboard:external_agent_view_install_guide")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </section>
      </div>
    </ModalShell>
  );
}
