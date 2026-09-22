import { AlertTriangle, Loader2, Lock, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

export type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export function LoadingCard({ label }: { label: string }) {
  return (
    <div
      className="flex items-center gap-2 rounded-[10px] border border-hairline px-5 py-6 text-text-3"
      style={CARD_STYLE}
    >
      <Loader2 aria-hidden className="h-3.5 w-3.5 text-accent-2 motion-safe:animate-spin" />
      <span className="text-[12.5px]">{label}</span>
    </div>
  );
}

export function ErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  const { t } = useTranslation("common");
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-[10px] border px-4 py-3.5"
      style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-tint)" }}
    >
      <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warm-bright" />
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] font-medium text-text">{title}</p>
        {message && <p className="mt-0.5 text-[12px] text-text-3">{message}</p>}
      </div>
      <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
        <RefreshCcw aria-hidden className="h-3.5 w-3.5" />
        {t("retry")}
      </button>
    </div>
  );
}

export function categoryLabel(t: (key: string, options: { defaultValue: string }) => string, category: string) {
  return t(`prompt_templates_category_${category}`, { defaultValue: category });
}

/** 锁定标记：模版或片段声明 `protected`，不提供编辑入口。 */
export function LockBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      title={t("prompt_templates_locked_hint")}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-hairline px-1.5 py-px font-sans text-[10.5px] leading-[1.5] text-text-3"
    >
      <Lock aria-hidden className="h-3 w-3" />
      {t("prompt_templates_locked")}
    </span>
  );
}
