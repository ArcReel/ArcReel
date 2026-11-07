export type AssetType = "character" | "scene" | "prop";

/** A derivative attached to a character asset in the asset library; transferred together with its base and displayed read-only by the frontend. */
export interface AssetDerivative {
  name: string;
  description: string;
  image_path: string | null;
}

export interface Asset {
  id: string;
  type: AssetType;
  name: string;
  description: string;
  voice_style: string;
  image_path: string | null;
  audio_path: string | null;
  source_project: string | null;
  updated_at: string | null;
  derivatives: AssetDerivative[];
}

/** One page of the asset library. `total` counts matches for the current type and search term; `counts` follows only the search term and gives matches for each of the three types. */
export interface AssetListPage {
  items: Asset[];
  total: number;
  counts: Record<AssetType, number>;
}

export interface AssetCreatePayload {
  type: AssetType;
  name: string;
  description?: string;
  voice_style?: string;
}

export interface AssetUpdatePayload {
  name?: string;
  description?: string;
  voice_style?: string;
}
