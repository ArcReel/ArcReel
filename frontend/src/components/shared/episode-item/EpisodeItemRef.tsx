import { cn } from "cn";

import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { episodeItemKind, isPositionedItemId, type EpisodeItemFacts } from "@/utils/episode-item";
import { useEpisodeItem } from "./useEpisodeItem";
import { ItemPositionBadge } from "./ItemPositionBadge";

export function EpisodeItemRef({ itemId, className }: { itemId: string; className?: string }) {
  const item = useEpisodeItem(itemId);
  if (!isPositionedItemId(itemId)) {
    return (
      <span
        translate="no"
        className={cn("rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs text-subtle-foreground", className)}
      >
        {itemIdWithinEpisode(itemId)}
      </span>
    );
  }
  return <EpisodeItemRefView item={item} kind={episodeItemKind(itemId)} className={className} />;
}

export function EpisodeItemRefView({
  item,
  kind,
  projectName,
  className,
}: {
  item: { position: number | null; excerpt: string; thumbnail: string | null } | null;
  kind: EpisodeItemFacts["kind"];
  projectName?: string;
  className?: string;
}) {
  if (!item || item.position === null) {
    return (
      <span translate="no" className={cn("inline-flex shrink-0 items-center", className)}>
        <ItemPositionBadge position={null} kind={kind} />
      </span>
    );
  }
  return (
    <span className={cn("inline-flex max-w-full min-w-0 items-center gap-1.5 align-middle", className)}>
      {item.thumbnail ? (
        <span className="relative inline-flex h-6 w-10 shrink-0 overflow-hidden rounded-sm bg-muted">
          <ItemThumbnail path={item.thumbnail} projectName={projectName} />
          <ItemPositionBadge position={item.position} kind={kind} overlay />
        </span>
      ) : (
        <ItemPositionBadge position={item.position} kind={kind} />
      )}
      {item.excerpt && <TruncatedText text={item.excerpt} className="text-xs text-subtle-foreground" />}
    </span>
  );
}

function ItemThumbnail({ path, projectName }: { path: string; projectName?: string }) {
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  const project = projectName ?? currentProjectName;
  const fingerprint = useProjectsStore((s) => (project === s.currentProjectName ? s.getAssetFingerprint(path) : null));
  if (!project) return null;
  return (
    <img
      src={API.getFileUrl(project, path, fingerprint, { width: 160 })}
      alt=""
      loading="lazy"
      decoding="async"
      className="size-full object-cover"
    />
  );
}
