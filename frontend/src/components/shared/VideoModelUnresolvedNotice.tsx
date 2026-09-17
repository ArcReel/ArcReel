import { AlertTriangle, ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

/**
 * 内容确认页的「视频模型未解析」提示：确认转出要按视频模型能力给分镜定时长档位，
 * 服务端明确答复模型未配置或无法解析时，确认必然被拒，先在确认按钮附近说明并指引到项目设置。
 */
export function VideoModelUnresolvedNotice({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-[10px] border border-amber-500/40 px-3.5 py-2.5"
    >
      <p className="flex items-center gap-1.5 text-[11.5px] text-amber-200">
        <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0 text-amber-400" />
        {t("review_video_model_unresolved_hint")}
      </p>
      <Link href={`~/app/projects/${encodeURIComponent(projectName)}/settings`} className={GHOST_BTN_CLS}>
        <ArrowRight className="h-3.5 w-3.5" />
        {t("review_video_model_unresolved_action")}
      </Link>
    </div>
  );
}
