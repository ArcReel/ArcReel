import { Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { projectSettingsPath } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";
import type { ProjectData } from "@/types";

const MODE_LABEL_KEY = {
  narration: "narration_visuals_mode",
  drama: "drama_animation_mode",
  ad: "ad_short_video_mode",
} as const;

/** 项目画幅；旧项目的分类画幅取分镜那一项，没有设置时不显示。 */
function aspectRatioOf(data: ProjectData): string | null {
  const raw = typeof data.aspect_ratio === "string" ? data.aspect_ratio : data.aspect_ratio?.storyboard;
  return raw || null;
}

function formatDuration(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

/** 概览页头：标题、元信息（模式 · 画幅 · 集数 · 脚本时长）与「项目设置」。 */
export function OverviewHeader({
  projectName,
  data,
  readOnly,
}: {
  projectName: string;
  data: ProjectData;
  readOnly: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const isAd = data.content_mode === "ad";
  const episodes = data.episodes ?? [];
  // 各集脚本条目的计划时长之和，与侧栏集卡上的时长同一口径，不代表已生成的视频。
  const scriptSeconds = episodes.reduce((sum, ep) => sum + (ep.duration_seconds ?? 0), 0);
  const aspect = aspectRatioOf(data);

  const meta = [
    t(MODE_LABEL_KEY[data.content_mode]),
    aspect === "9:16" ? t("portrait_9_16") : aspect === "16:9" ? t("landscape_16_9") : aspect,
    isAd ? null : episodes.length > 0 ? t("overview_meta_episodes", { count: episodes.length }) : t("overview_meta_no_episodes"),
    scriptSeconds > 0 ? t("overview_meta_script_duration", { duration: formatDuration(scriptSeconds) }) : null,
  ].filter(Boolean);

  return (
    <header className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="display-serif text-2xl font-semibold tracking-tight break-words text-foreground">
          {data.title}
        </h1>
        <p className="num mt-1 text-sm text-muted-foreground">{meta.join(" · ")}</p>
      </div>
      {readOnly ? null : (
        <Link
          href={`~${projectSettingsPath(projectName)}`}
          className={buttonVariants({ variant: "ghost", size: "sm", className: "shrink-0" })}
        >
          <Settings2 aria-hidden data-icon="inline-start" />
          {t("project_settings")}
        </Link>
      )}
    </header>
  );
}
