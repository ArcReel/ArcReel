import type { MediaType } from "./custom-provider";

/** One model catalog search result. `maker`, `name`, and `media_type` jointly identify the entry; the interface shows only the display name. */
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

/** Hints accompanying a manually entered new entry name: an existing model of the same name in the catalog (select it directly when present), and optional video durations prefilled from name-based presets. */
export interface NewEntryHints {
  existing: CatalogEntryHit | null;
  durations: number[] | null;
}
