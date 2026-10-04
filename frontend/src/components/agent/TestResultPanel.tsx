import { AlertCircle, CheckCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import type { TestConnectionResponse } from "@/types/agent-credential";

interface Props {
  result: TestConnectionResponse;
}

/** Agent 供应商连接测试的结果：结论、诊断建议、实际请求的地址与响应，失败时可展开原始报错。 */
export function TestResultPanel({ result }: Props) {
  const { t } = useTranslation("dashboard");
  const { overall, messages_probe, diagnosis, messages_url } = result;
  const ok = overall === "ok";
  const Icon = ok ? CheckCircle : AlertCircle;

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex flex-col gap-2 rounded-lg border p-3 text-sm",
        ok ? "border-good/40 bg-good/5" : "border-warn/40 bg-warn/5",
      )}
    >
      <p className={cn("flex items-center gap-2 font-medium", ok ? "text-good" : "text-warn")}>
        <Icon aria-hidden className="size-4" />
        {t(ok ? "test_ok" : "test_fail")}
      </p>

      {diagnosis && <p className="text-subtle-foreground">{t(`diagnosis_${diagnosis}`)}</p>}

      {/* 探测的就是 Agent 运行时调用的地址，二者同一个字符串 */}
      <div className="flex flex-col gap-0.5 text-xs text-muted-foreground">
        <span>{t("messages_url")}</span>
        <TruncatedText text={messages_url} className="font-mono" />
        <span className="font-mono tabular-nums">
          {t("cred_test_response", {
            status: messages_probe.status_code ?? "—",
            latency: messages_probe.latency_ms ?? "—",
          })}
        </span>
      </div>

      {messages_probe.error && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">{t("raw_error")}</summary>
          <pre className="relative mt-1 max-h-40 overflow-auto font-mono break-all whitespace-pre-wrap">
            {messages_probe.error}
          </pre>
        </details>
      )}
    </div>
  );
}
