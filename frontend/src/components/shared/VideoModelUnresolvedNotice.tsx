import { AlertTriangle, ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { projectSettingsPath } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";

/**
 * 内容确认页的「视频模型未解析」提示：确认转出要按视频模型能力给分镜定时长档位，
 * 服务端明确答复模型未配置或无法解析时，确认必然被拒，先在确认按钮附近说明并指引到项目设置。
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
