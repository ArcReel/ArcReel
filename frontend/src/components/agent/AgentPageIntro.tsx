import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
import { ArrowUpRight, Bot } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";

interface AgentPageIntroProps {
  onOpenExternalGuide: () => void;
}

export function AgentPageIntro({ onOpenExternalGuide }: AgentPageIntroProps) {
  const { t } = useTranslation("dashboard");
  return (
    <section aria-labelledby="agent-access-title">
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-primary">
        Agent Access
      </div>
      <h2 id="agent-access-title" className="font-editorial mt-1 text-2xl text-foreground">
        {t("agent_access_title")}
      </h2>
      <p className="mt-1.5 max-w-2xl text-[12.5px] leading-[1.55] text-muted-foreground">
        {t("agent_access_desc")}
      </p>

      <div
        className="mt-4 grid overflow-hidden rounded-lg border border-border sm:grid-cols-2"
        style={CARD_STYLE}
      >
        <div className="flex gap-3.5 border-b border-border/50 p-4 sm:border-b-0 sm:border-r">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-border/50 bg-card/55">
            <ClaudeColor size={22} />
          </div>
          <div className="min-w-0">
            <h3 className="text-[13.5px] font-medium text-foreground">{t("embedded_agent")}</h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("embedded_agent_desc")}
            </p>
          </div>
        </div>

        <div className="flex gap-3.5 p-4">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-primary/25 bg-primary/12 text-primary">
            <Bot className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0">
            <h3 className="text-[13.5px] font-medium text-foreground">{t("external_agent")}</h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground">
              {t("external_agent_desc")}
            </p>
            <button
              type="button"
              onClick={onOpenExternalGuide}
              className={`${GHOST_BTN_CLS} mt-3`}
            >
              {t("external_agent_guide")}
              <ArrowUpRight className="h-3 w-3" aria-hidden />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
