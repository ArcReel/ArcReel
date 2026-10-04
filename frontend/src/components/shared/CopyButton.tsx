import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copyText } from "@/utils/clipboard";

interface CopyButtonProps {
  text: string;
  /** 覆盖未复制状态的按钮无障碍名称。 */
  label?: string;
  /** 覆盖复制成功后的按钮无障碍名称。 */
  copiedLabel?: string;
  className?: string;
}

/** 图标按钮：颜色随所在的行，复制成功后图标短暂变为品牌色对勾。 */
export function CopyButton({ text, label, copiedLabel, className }: CopyButtonProps) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const title = copied
    ? (copiedLabel ?? t("message_copied"))
    : (label ?? t("message_copy"));

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            className={className}
            aria-label={title}
            onClick={() => {
              // 复制成功才给对勾：非安全上下文走 execCommand 兜底，兜底也失败时不假报成功
              void copyText(text).then(
                () => setCopied(true),
                () => undefined,
              );
            }}
          />
        }
      >
        {copied ? <Check aria-hidden className="text-primary" /> : <Copy aria-hidden />}
      </TooltipTrigger>
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
}
