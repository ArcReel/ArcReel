import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 资产图的生成按钮：生成中禁用，前置图标换成转圈。 */
export function GenerateButton({
  onClick,
  loading = false,
  label,
  className,
  disabled = false,
}: {
  onClick: () => void;
  loading?: boolean;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation("assets");
  return (
    <Button onClick={onClick} disabled={disabled || loading} className={className}>
      {loading ? (
        <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
      ) : (
        <Sparkles aria-hidden data-icon="inline-start" />
      )}
      {loading ? t("gallery_generating") : label}
    </Button>
  );
}
