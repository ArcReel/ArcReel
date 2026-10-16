import type { MediaType } from "./custom-provider";

/** 模型目录搜索命中的一个条目。`maker`、`name` 与 `media_type` 合起来标识条目，界面只展示显示名。 */
export interface CatalogEntryHit {
  maker: string;
  name: string;
  media_type: MediaType;
  display_name: string;
  discontinued: boolean;
}

export interface CatalogSearchResponse {
  entries: CatalogEntryHit[];
}

/** 手动填写新条目时随名字给出的提示：目录里已有的同名模型（有就应直接选择），视频模型按名字预设预填的可选时长。 */
export interface NewEntryHints {
  existing: CatalogEntryHit | null;
  durations: number[] | null;
}
