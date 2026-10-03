import { Bot, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DROPDOWN_PANEL_STYLE, ICON_BTN_FILLED_CLS } from "@/components/shared/darkroom-tokens";
import { ModalShell } from "@/components/legacy/ModalShell";

import { ExternalAgentGuide } from "./ExternalAgentGuide";

interface ExternalAgentModalProps {
  onClose: () => void;
}

export function ExternalAgentModal({ onClose }: ExternalAgentModalProps) {
  const { t } = useTranslation(["dashboard", "common"]);

  return (
    <ModalShell
      open
      onClose={onClose}
      labelledBy="external-agent-modal-title"
      describedBy="external-agent-modal-subtitle"
      className="z-10 flex max-h-[90vh] w-full max-w-lg flex-col overflow-y-auto overscroll-contain rounded-2xl border border-border shadow-2xl shadow-black/60"
      style={DROPDOWN_PANEL_STYLE}
    >
      <div
        className="sticky top-0 z-10 flex items-center justify-between border-b border-border px-5 py-4"
        style={DROPDOWN_PANEL_STYLE}
      >
        <div className="flex items-center gap-2.5">
          <Bot className="h-5 w-5 text-primary" aria-hidden />
          <div>
            <h2 id="external-agent-modal-title" className="text-[14px] font-semibold text-foreground">
              {t("dashboard:external_agent_guide")}
            </h2>
            <p id="external-agent-modal-subtitle" className="text-[12px] text-muted-foreground">
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

      <ExternalAgentGuide className="p-5" />
    </ModalShell>
  );
}
