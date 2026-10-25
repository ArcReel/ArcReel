import { episodeEditViewPath, projectSettingsPath } from "@/app-routes";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import type { FileViewEntry, FileViewOwner, WorkspaceFocusTargetInput } from "@/types";
import { assetNotificationTarget } from "@/utils/task-target";

export type FilePreviewKind = "image" | "video" | "audio" | "text";

export function canDeleteFile(entry: FileViewEntry): boolean {
  if (entry.type !== "file") return false;
  // 当前版本快照由服务端标出，删除会被拒绝
  return (entry.role === "history" && !entry.is_current) || (entry.role === "unreferenced" && entry.owner?.kind !== "source");
}

const PREVIEW_KINDS: Record<string, FilePreviewKind> = {
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  mp4: "video",
  webm: "video",
  mov: "video",
  wav: "audio",
  mp3: "audio",
  m4a: "audio",
  ogg: "audio",
  txt: "text",
  md: "text",
  json: "text",
  py: "text",
  yaml: "text",
  yml: "text",
  srt: "text",
  csv: "text",
  log: "text",
};

/** 文本预览的上限：更大的文件只提供下载，不把整份文本塞进页面。 */
export const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;

export function previewKind(name: string): FilePreviewKind | null {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? null : (PREVIEW_KINDS[name.slice(dot + 1).toLowerCase()] ?? null);
}

/** 「跳到所属界面」的去处：`href` 是项目工作区内的地址（项目设置以 `~` 开头），`focus` 是到达后要定位的对象。 */
export interface OwnerTarget {
  href: string;
  labelKey: string;
  focus?: WorkspaceFocusTargetInput;
}

export function ownerTarget(owner: FileViewOwner | null | undefined, projectName: string): OwnerTarget | null {
  if (!owner) return null;
  const episodePath = owner.episode === undefined ? null : `/episodes/${owner.episode}`;
  switch (owner.kind) {
    case "asset": {
      if (!owner.asset_type || !owner.id) return null;
      const target = assetNotificationTarget(owner.asset_type, owner.id);
      return { href: target.route, labelKey: "file_view_open_asset", focus: target };
    }
    case "segment":
    case "reference_unit":
    case "grid": {
      if (!episodePath || !owner.id) return null;
      const focus = { type: owner.kind, id: owner.id, route: episodePath };
      return { href: episodePath, labelKey: `file_view_open_${owner.kind}`, focus };
    }
    case "episode":
      return episodePath ? { href: episodePath, labelKey: "file_view_open_episode" } : null;
    case "timeline":
      return owner.episode === undefined
        ? null
        : { href: episodeEditViewPath(owner.episode, owner.id), labelKey: "file_view_open_timeline" };
    case "source":
      return { href: episodesViewPath(), labelKey: "file_view_open_source" };
    case "memory":
      return {
        href: `~${projectSettingsPath(projectName, "memory", owner.id ? { file: owner.id } : {})}`,
        labelKey: "file_view_open_memory",
      };
    case "project":
      return { href: `~${projectSettingsPath(projectName)}`, labelKey: "file_view_open_project" };
  }
}
