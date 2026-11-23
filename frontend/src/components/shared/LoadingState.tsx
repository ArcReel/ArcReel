import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

export function LoadingState({ variant }: { variant: "screen" | "pane" }) {
  const { t } = useTranslation("common");
  return (
    <div
      role="status"
      aria-live="polite"
      className={
        variant === "screen"
          ? "flex h-dvh items-center justify-center gap-2 bg-background text-sm text-muted-foreground"
          : "flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"
      }
    >
      <Loader2 aria-hidden className="size-4 animate-spin" />
      <span>{t("loading")}</span>
    </div>
  );
}
