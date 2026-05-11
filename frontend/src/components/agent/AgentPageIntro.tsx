import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
import { Terminal } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

export function AgentPageIntro() {
  const { t } = useTranslation("dashboard");
  return (
    <div>
      <div className="flex items-start gap-4">
        <div
          className="shrink-0 rounded-[10px] border border-hairline p-3"
          style={{
            ...CARD_STYLE,
            boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
          }}
        >
          <ClaudeColor size={28} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
            Anthropic Bridge
          </div>
          <h2
            className="font-editorial mt-1"
            style={{
              fontWeight: 400,
              fontSize: 24,
              lineHeight: 1.1,
              letterSpacing: "-0.012em",
              color: "var(--color-text)",
            }}
          >
            {t("arcreel_agent")}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-[1.55] text-text-3">
            {t("agent_sdk_desc")}
          </p>
        </div>
      </div>
      <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2">
        <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-text-4" aria-hidden />
        <p className="text-[11.5px] leading-[1.55] text-text-3">
          {t("claude_code_compat_hint")}
        </p>
      </div>
    </div>
  );
}
