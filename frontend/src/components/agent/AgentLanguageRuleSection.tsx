import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { SectionShell } from "@/components/ui/SectionShell";
import { errMsg } from "@/utils/async";

const AGENT_LANGUAGE_RULE_ID = "text/agent_language_rule";

/** Agent 会话启动时拼进系统提示的语言规范，只读展示其模版正文。 */
export function AgentLanguageRuleSection() {
  const { t } = useTranslation("dashboard");
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.getPromptTemplate(AGENT_LANGUAGE_RULE_ID, { signal: controller.signal })
      .then((detail) => setSource(detail.source))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errMsg(err));
      });
    return () => controller.abort();
  }, []);

  return (
    <SectionShell
      kicker="Language Rule"
      title={t("agent_language_rule_title")}
      description={t("agent_language_rule_desc")}
    >
      {error ? (
        <p role="alert" className="text-[12px] text-danger-2">
          {t("agent_language_rule_load_failed", { message: error })}
        </p>
      ) : source === null ? (
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
      ) : (
        <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-[1.6] text-text-2">
          {source}
        </pre>
      )}
    </SectionShell>
  );
}
