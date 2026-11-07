import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Loading placeholder: spinner and Loading, with `role="status"` for screen-reader announcements.
 *
 * - `screen`: fills the viewport while authentication is unresolved or a whole-page lazy chunk has not arrived.
 * - `pane`: fills the parent container for lazy loading of local regions such as workspace canvases.
 */
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
