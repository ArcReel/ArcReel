import type { AssetSheetType } from "@/types";

/**
 * 资产图端点槽位类型：本体任务用资产类型本身作 task_type；角色衍生恒为 ``character_derivative``，
 * 与本体独立的默认端点槽位（docs/adr/0098）。
 */
export type AssetImageEndpointType = AssetSheetType | "character_derivative";

export const ASSET_IMAGE_SLOT_KEY: Record<AssetImageEndpointType, string> = {
  character: "default_image_backend_character",
  character_derivative: "default_image_backend_character_derivative",
  scene: "default_image_backend_scene",
  prop: "default_image_backend_prop",
  product: "default_image_backend_product",
};

/** 当前生效端点来自哪一层，与后端 ``_resolve_layered_backend`` 的图片解析链同口径。 */
export type AssetImageEndpointSource =
  | "project_slot"
  | "project_bucket"
  | "project_default"
  | "global_slot"
  | "global_bucket"
  | "global_default"
  | "auto";

export interface ResolvedAssetImageEndpoint {
  /** 生效端点 ``"provider/model"``；空串 = 未配置、交由自动推断。 */
  value: string;
  source: AssetImageEndpointSource;
}

interface ResolutionLayer {
  value: unknown;
  source: AssetImageEndpointSource;
}

/**
 * 前端镜像后端图片端点解析链，只用于「当前会执行哪个端点」的展示与默认端点落写。
 *
 * 优先级：项目资产槽位 > 项目桶（``image_provider_<t2i|t2i>``）> 项目默认
 * （``default_image_backend``）> 全局资产槽位 > 全局桶（``default_image_backend_<t2i|i2i>``）
 * > 全局默认（``default_image_backend``）> 自动推断。payload 层（本次生成的临时覆盖）不在此
 * 链内：它由调用点单独持有，生效端点只反映未覆盖时的配置。
 */
export function resolveAssetImageEndpoint(args: {
  assetType: AssetImageEndpointType;
  useImageToImage: boolean;
  project: object | null | undefined;
  globalSettings: object | null | undefined;
}): ResolvedAssetImageEndpoint {
  const slotKey = ASSET_IMAGE_SLOT_KEY[args.assetType];
  const bucketKey = args.useImageToImage ? "i2i" : "t2i";
  const project = (args.project ?? {}) as Record<string, unknown>;
  const globalSettings = (args.globalSettings ?? {}) as Record<string, unknown>;

  const layers: ResolutionLayer[] = [
    { value: project[slotKey], source: "project_slot" },
    { value: project[`image_provider_${bucketKey}`], source: "project_bucket" },
    { value: project["default_image_backend"], source: "project_default" },
    { value: globalSettings[slotKey], source: "global_slot" },
    { value: globalSettings[`default_image_backend_${bucketKey}`], source: "global_bucket" },
    { value: globalSettings["default_image_backend"], source: "global_default" },
  ];
  for (const layer of layers) {
    const value = typeof layer.value === "string" ? layer.value.trim() : "";
    if (value) return { value, source: layer.source };
  }
  return { value: "", source: "auto" };
}
