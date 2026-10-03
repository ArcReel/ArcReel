import { useEffect, useId, useState } from "react";
import { Check, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { copyText } from "@/utils/clipboard";

const COPIED_RESET_MS = 2000;

interface CopyableValueProps {
  /** 值上方的可见标签；不传时由 copyLabel 单独说明按钮。 */
  label?: string;
  value: string;
  /** 复制按钮的可访问名称，写明复制的对象，如「复制安装命令」。 */
  copyLabel: string;
  /** 长文本（如提示词）按行折行显示；默认单行、在词内断开。 */
  multiline?: boolean;
  className?: string;
}

/**
 * 只读的命令、地址或令牌，旁边一个复制按钮。
 * 复制成功后按钮短暂显示「已复制」并经 status 播报；剪贴板不可用时弹错误提示，内容保持可选中以便手动复制。
 */
export function CopyableValue({ label, value, copyLabel, multiline = false, className }: CopyableValueProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [copied, setCopied] = useState(false);
  const labelId = useId();

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const handleCopy = () => {
    void copyText(value).then(
      () => setCopied(true),
      () => {
        setCopied(false);
        useAppStore.getState().pushToast(t("dashboard:access_copy_failed"), "error");
      },
    );
  };

  return (
    <div role="group" aria-labelledby={label ? labelId : undefined} className={cn("flex flex-col gap-1.5", className)}>
      {label && (
        <span id={labelId} className="text-xs font-medium text-muted-foreground">
          {label}
        </span>
      )}
      <div className="flex items-start gap-2 rounded-lg border border-border bg-background py-1.5 pr-1.5 pl-3">
        <code
          translate="no"
          className={cn(
            "min-w-0 flex-1 self-center font-mono text-xs/5 text-foreground",
            multiline ? "whitespace-pre-wrap wrap-break-word" : "break-all",
          )}
        >
          {value}
        </code>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleCopy}
          // 「已复制」时去掉 aria-label，让可访问名称与可见文字一致（WCAG 2.5.3）
          aria-label={copied ? undefined : copyLabel}
          className="shrink-0"
        >
          {copied ? <Check aria-hidden data-icon="inline-start" /> : <Copy aria-hidden data-icon="inline-start" />}
          {copied ? t("common:copied") : t("common:copy")}
        </Button>
      </div>
      <span role="status" className="sr-only">
        {copied ? t("common:copied") : ""}
      </span>
    </div>
  );
}
