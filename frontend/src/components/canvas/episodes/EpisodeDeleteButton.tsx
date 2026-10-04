import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
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
      <Button variant="ghost" size="xs" className="ml-auto" onClick={() => void deletion.requestDelete(episode)}>
        <Trash2 aria-hidden data-icon="inline-start" />
        {t("episode_menu_delete")}
      </Button>
      {deletion.dialog}
    </>
  );
}
