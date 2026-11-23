import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";

import { API } from "@/api";
import { EpisodeSourceText } from "@/components/canvas/EpisodeSourceReview";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";
import { episodeDisplayName } from "@/utils/episode-display";

import { canViewEpisodeSource } from "./episode-view";

type SourceRead = { status: "loading" } | { status: "ready"; text: string } | { status: "failed" };

function SourceContent({ projectName, episode }: { projectName: string; episode: number }) {
  const { t } = useTranslation("dashboard");
  const [read, setRead] = useState<SourceRead>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    void API.getSourceContent(projectName, `episode_${episode}.txt`, { signal: controller.signal }).then(
      (text) => {
        if (!controller.signal.aborted) setRead({ status: "ready", text });
      },
      () => {
        if (!controller.signal.aborted) setRead({ status: "failed" });
      },
    );
    return () => controller.abort();
  }, [projectName, episode]);

  if (read.status === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("episode_source_view_loading")}
      </p>
    );
  }
  if (read.status === "failed") {
    return (
      <p role="alert" className="text-sm text-destructive">
        {t("episode_source_view_failed")}
      </p>
    );
  }
  if (read.text.trim() === "") {
    return <p className="text-sm text-muted-foreground">{t("episode_source_view_empty")}</p>;
  }
  return <EpisodeSourceText text={read.text} />;
}

export function EpisodeSourceViewer({
  projectName,
  episode,
  projectData,
  script,
  demo,
}: {
  projectName: string;
  episode: number;
  projectData: ProjectData | null;
  script: EpisodeScript | null;
  demo: boolean;
}) {
  const { t } = useTranslation("dashboard");
  if (!canViewEpisodeSource(projectData, episode, script, demo)) return null;
  const title = t("episode_workspace_source_title");
  return (
    <Dialog>
      <DialogTrigger render={<Button variant="ghost" size="sm" className="shrink-0" />}>
        <FileText aria-hidden data-icon="inline-start" />
        {t("episode_source_view")}
      </DialogTrigger>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{episodeDisplayName(projectData?.episodes ?? [], episode, t)}</DialogDescription>
        </DialogHeader>
        <DialogBody tabIndex={0} role="region" aria-label={title}>
          <SourceContent projectName={projectName} episode={episode} />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
