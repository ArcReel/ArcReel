export type AssetType = "character" | "scene" | "prop";

/** 资产库里挂在一条角色资产下的衍生；随本体整套进出，前端只读展示。 */
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
