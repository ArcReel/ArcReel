export type AssetType = "character" | "scene" | "prop";

export interface Asset {
  id: string;
  type: AssetType;
  name: string;
  description: string;
  voice_style: string;
  image_path: string | null;
  source_project: string | null;
  updated_at: string | null;
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
