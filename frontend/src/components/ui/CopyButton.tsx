import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { copyText } from "@/utils/clipboard";

interface CopyButtonProps {
  text: string;
  /** 覆盖默认的按钮无障碍名称（默认「复制」/「已复制」）。 */
  label?: string;
  className?: string;
}

/** 无底色图标按钮：hover 出灰色圆角方形背景，复制成功后图标短暂变对勾。 */
export function CopyButton({ text, label, className = "" }: CopyButtonProps) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const Icon = copied ? Check : Copy;
  const title = copied ? t("message_copied") : (label ?? t("message_copy"));

  return (
    <button
      type="button"
      onClick={() => {
        // 复制成功才给对勾：非安全上下文走 execCommand 兜底，兜底也失败时不假报成功
        void copyText(text).then(
          () => setCopied(true),
          () => undefined,
        );
      }}
      title={title}
      aria-label={title}
      className={`focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10 ${className}`}
      style={{ color: copied ? "var(--color-accent-2)" : "var(--color-text-3)" }}
    >
      <Icon aria-hidden className="h-3.5 w-3.5" />
    </button>
  );
}
