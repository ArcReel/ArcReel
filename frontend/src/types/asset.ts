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

/** 资产库列表的一页。`total` 是当前类型与搜索词下的匹配总数；`counts` 只跟随搜索词，三个类型各自的匹配数。 */
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
