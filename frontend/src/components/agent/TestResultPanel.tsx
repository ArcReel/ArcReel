import { AlertCircle, CheckCircle, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { TestConnectionResponse } from "@/types/agent-credential";

type Overall = TestConnectionResponse["overall"];

const OVERALL_VIEW: Record<Overall, { Icon: LucideIcon; tone: string; headlineKey: string }> = {
  ok: {
    Icon: CheckCircle,
    tone: "border-accent/40 bg-accent/5 text-accent",
    headlineKey: "test_ok",
  },
  fail: {
    Icon: AlertCircle,
    tone: "border-warm-bright/40 bg-warm-bright/5 text-warm-bright",
    headlineKey: "test_fail",
  },
};

interface Props {
  result: TestConnectionResponse;
  /** true = 紧贴在凭证 row 下方,顶部圆角拉平,无 top margin. */
  attached?: boolean;
}

export function TestResultPanel({ result, attached = false }: Props) {
  const { t } = useTranslation("dashboard");
  const { overall, messages_probe, diagnosis, messages_url } = result;

  const { Icon, tone, headlineKey } = OVERALL_VIEW[overall];

  return (
    <div
      className={`${attached ? "rounded-t-none border-t-0" : "mt-3"} rounded-[10px] border p-3 ${tone}`}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 text-[12.5px] font-medium">
        <Icon className="h-4 w-4" aria-hidden />
        {t(headlineKey)}
      </div>

      {diagnosis && (
        <div className="mt-2 text-[12px] leading-[1.55] text-text-2">
          {t(`diagnosis_${diagnosis}`)}
        </div>
      )}

      {/* 探测的就是 Agent 运行时调用的地址，二者同一个字符串 */}
      <div className="mt-2 font-mono text-[10.5px] text-text-4 tabular-nums">
        <div className="uppercase tracking-[0.12em]">{t("messages_url")}</div>
        <div className="truncate text-text-3">{messages_url}</div>
        <div>
          POST · {messages_probe.status_code ?? "—"} · {messages_probe.latency_ms ?? "—"}&nbsp;ms
        </div>
      </div>

      {messages_probe.error && (
        <details className="mt-2 text-[11px] text-text-4">
          <summary className="cursor-pointer">{t("raw_error")}</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">
            {messages_probe.error}
          </pre>
        </details>
      )}
    </div>
  );
}
