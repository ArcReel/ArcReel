export type AssetType = "character" | "scene" | "prop";

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

export type AssetIdentity = Pick<Asset, "id" | "type" | "name">;

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
