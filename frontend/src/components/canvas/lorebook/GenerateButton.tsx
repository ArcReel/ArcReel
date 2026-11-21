import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

export function GenerateButton({
  onClick,
  loading = false,
  label,
  className,
  disabled = false,
  size,
  variant,
}: {
  onClick: () => void;
  loading?: boolean;
  label: string;
  className?: string;
  disabled?: boolean;
  size?: ComponentProps<typeof Button>["size"];
  /** 同一区域已有主操作时（如详情里每条衍生的生成）用次要变体。 */
  variant?: ComponentProps<typeof Button>["variant"];
}) {
  const { t } = useTranslation("assets");
  return (
    <Button onClick={onClick} disabled={disabled || loading} size={size} variant={variant} className={className}>
      {loading ? (
        <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
      ) : (
        <Sparkles aria-hidden data-icon="inline-start" />
      )}
      {loading ? t("gallery_generating") : label}
    </Button>
  );
}
