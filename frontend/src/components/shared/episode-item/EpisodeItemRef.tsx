import { cn } from "cn";

import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { episodeItemKind, isPositionedItemId, type EpisodeItemFacts } from "@/utils/episode-item";
import { useEpisodeItem } from "./useEpisodeItem";
import { ItemPositionBadge } from "./ItemPositionBadge";

/**
 * 列表之外指认一个分镜或视频单元：位置角标、缩略图与正文前十几个字。
 *
 * 位置按当前项目剧本现算，条目已不在剧本里时只写「已删除」。没有缩略图时角标单独成行内标签。
 * 宫格这类不按位置指称的条目仍显示集内 ID。
 */
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

/**
 * 已解析出条目事实时的呈现，供拿不到项目剧本、由后端给出位置的调用方复用（用量记录跨项目，
 * 传入记录所属的 `projectName` 取缩略图）。`item` 为 null 或位置为 null 表示条目已不在剧本里。
 */
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
  // 资产指纹只对当前项目有效；其他项目的缩略图不带指纹。
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
