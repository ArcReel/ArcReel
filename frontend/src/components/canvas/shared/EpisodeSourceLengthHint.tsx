import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { API } from "@/api";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { episodeSourceCharCount, LONG_EPISODE_SOURCE_THRESHOLD } from "@/utils/text-task-progress";

export function EpisodeSourceLengthHint({ episode, count }: { episode: number; count: number }) {
  const { t, i18n } = useTranslation("dashboard");
  if (count <= LONG_EPISODE_SOURCE_THRESHOLD) return null;
  return (
    <div className="flex items-start gap-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-subtle-foreground">
      <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
      <p className="min-w-0 wrap-break-word">
        {t("script_plan_long_source_hint", {
          count: count.toLocaleString(i18n.language),
          threshold: LONG_EPISODE_SOURCE_THRESHOLD.toLocaleString(i18n.language),
        })}{" "}
        <Link
          href={episodesViewPath({ episode })}
          className="focus-ring rounded-sm text-primary underline underline-offset-2"
        >
          {t("episode_workspace_source_go_episodes")}
        </Link>
      </p>
    </div>
  );
}

export function SavedEpisodeSourceLengthHint({ projectName, episode }: { projectName: string; episode: number }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void API.getSourceContent(projectName, `episode_${episode}.txt`, { signal: controller.signal }).then(
      (text) => { if (!controller.signal.aborted) setCount(episodeSourceCharCount(text)); },
      () => {},
    );
    return () => controller.abort();
  }, [projectName, episode]);
  return <EpisodeSourceLengthHint episode={episode} count={count} />;
}
