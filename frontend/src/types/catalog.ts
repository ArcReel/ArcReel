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
