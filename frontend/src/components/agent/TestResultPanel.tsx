import { AlertCircle, AlertTriangle, ArrowRight, CheckCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { TestConnectionResponse } from "@/types/agent-credential";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE } from "@/components/ui/darkroom-tokens";

interface Props {
  /**
   * 触发本次测试时用户看到 / 用的 base_url。可能是空（draft 模式新建预设凭证）；
   * 当 suggestion 为 replace_base_url 时用于 Before/After diff 显示。
   */
  originalBaseUrl?: string | null;
  result: TestConnectionResponse;
  onApplyFix?: (suggestedBaseUrl: string) => void;
}

export function TestResultPanel({ originalBaseUrl, result, onApplyFix }: Props) {
  const { t } = useTranslation("dashboard");
  const {
    overall,
    messages_probe,
    discovery_probe,
    diagnosis,
    suggestion,
    derived_messages_root,
    derived_discovery_root,
  } = result;

  const Icon = overall === "ok" ? CheckCircle : overall === "warn" ? AlertTriangle : AlertCircle;
  // 用项目内既有 utility（accent / warm-bright）+ 中性 oklch alpha 表达 ok/warn/fail
  const tone =
    overall === "ok"
      ? "border-accent/40 bg-accent/5 text-accent"
      : overall === "warn"
        ? "border-amber-300/40 bg-amber-300/5 text-amber-200"
        : "border-warm-bright/40 bg-warm-bright/5 text-warm-bright";

  const headlineKey =
    overall === "ok" ? "test_ok" : overall === "warn" ? "test_warn" : "test_fail";

  const suggestedBaseUrl =
    suggestion?.kind === "replace_base_url" ? (suggestion.suggested_value ?? null) : null;
  const hasReplaceFix = suggestedBaseUrl !== null && !!onApplyFix;

  return (
    <div className={`mt-3 rounded-[10px] border p-3 ${tone}`} role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-[12.5px] font-medium">
        <Icon className="h-4 w-4" aria-hidden />
        {t(headlineKey)}
      </div>

      {diagnosis && (
        <div className="mt-2 text-[12px] leading-[1.55] text-text-2">
          {t(`diagnosis_${diagnosis}`)}
        </div>
      )}

      {/* Before / After diff — 一键修复的视觉重锤 */}
      {hasReplaceFix && suggestedBaseUrl && onApplyFix && (
        <div className="mt-2.5 rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-2.5">
          {originalBaseUrl && (
            <div className="flex items-center gap-2 font-mono text-[10.5px]">
              <span className="w-10 shrink-0 uppercase tracking-[0.12em] text-text-4">from</span>
              <span className="truncate text-text-4 line-through decoration-warm-bright/60">
                {originalBaseUrl}
              </span>
            </div>
          )}
          <div className="mt-1 flex items-center gap-2 font-mono text-[10.5px]">
            <span className="w-10 shrink-0 uppercase tracking-[0.12em] text-accent">to</span>
            <span className="truncate text-text">{suggestedBaseUrl}</span>
          </div>
          <div className="mt-2.5 flex justify-end">
            <button
              type="button"
              onClick={() => onApplyFix(suggestedBaseUrl)}
              className={ACCENT_BTN_SM_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {t("apply_fix")}
              <ArrowRight className="h-3 w-3" aria-hidden />
            </button>
          </div>
        </div>
      )}

      {/* Probe 结果 — 调用 / 发现端点 */}
      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10.5px] text-text-4">
        <div>
          <div className="uppercase tracking-[0.12em]">{t("derived_messages_root")}</div>
          <div className="truncate text-text-3">{derived_messages_root}</div>
          <div className="text-text-4">
            POST · {messages_probe.status_code ?? "—"} · {messages_probe.latency_ms ?? "—"}ms
          </div>
        </div>
        <div>
          <div className="uppercase tracking-[0.12em]">{t("derived_discovery_root")}</div>
          <div className="truncate text-text-3">{derived_discovery_root || "—"}</div>
          <div className="text-text-4">
            GET ·{" "}
            {discovery_probe
              ? `${discovery_probe.status_code ?? "—"} · ${discovery_probe.latency_ms ?? "—"}ms`
              : "—"}
          </div>
        </div>
      </div>

      {messages_probe.error && (
        <details className="mt-2 text-[11px] text-text-4">
          <summary className="cursor-pointer">raw error</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all">{messages_probe.error}</pre>
        </details>
      )}
    </div>
  );
}
