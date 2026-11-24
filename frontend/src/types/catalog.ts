import type { MediaType } from "./custom-provider";

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

export interface NewEntryHints {
  existing: CatalogEntryHit | null;
  durations: number[] | null;
}
