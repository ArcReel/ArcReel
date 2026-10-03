import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { Trash2 } from "lucide-react";

import { useProjectsStore } from "@/stores/projects-store";

import { episodesViewPath } from "./episodes-view-model";
import { useDeleteEpisode } from "./useDeleteEpisode";

/** 集页头部的「删除这一集」：确认框列出服务端成文的丢失清单，删除后回到「分集」视图。广告/短片项目不显示。 */
export function EpisodeDeleteButton({ episode }: { episode: number }) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const projectName = useProjectsStore((s) => s.currentProjectName);
  const isAd = useProjectsStore((s) => s.currentProjectData?.content_mode === "ad");
  const deletion = useDeleteEpisode(projectName ?? "", () => setLocation(episodesViewPath()));
  if (!projectName || isAd) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => void deletion.requestDelete(episode)}
        className="focus-ring ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-warn"
        title={t("episode_menu_delete")}
      >
        <Trash2 className="h-3 w-3" aria-hidden />
        {t("episode_menu_delete")}
      </button>
      {deletion.dialog}
    </>
  );
}
