import { AlertTriangle, ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { projectSettingsPath } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";

/**
 * The content-confirmation page's unresolved-video-model notice: confirmation converts storyboard durations to tiers supported by the video model.
 * When the server explicitly reports an unconfigured or unresolvable model, confirmation will be rejected; explain this near the confirmation button and link to project settings first.
 */
export function VideoModelUnresolvedNotice({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-2.5"
    >
      <p className="flex items-center gap-1.5 text-xs text-subtle-foreground">
        <AlertTriangle aria-hidden className="size-3.5 shrink-0 text-warn" />
        {t("review_video_model_unresolved_hint")}
      </p>
      <Link
        href={`~${projectSettingsPath(projectName, "models")}`}
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        <ArrowRight aria-hidden data-icon="inline-start" />
        {t("review_video_model_unresolved_action")}
      </Link>
    </div>
  );
}
