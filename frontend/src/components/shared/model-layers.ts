/** 「默认模型 + 按用途指定模型」的层级演算（docs/adr/0054），`LayeredModelFields` 的调用方按各层键位调用。 */

import type { AutoBackendsResponse } from "@/types/system";

/**
 * 层级解析（docs/adr/0054）：取第一个非空层作为生效值。全层皆空即自动推断——那需要
 * 供应商就绪状态与注册表顺序，前端算不出，返回 undefined；实际选中的模型由后端预览
 * （`AutoBackendsResponse`）给出，按执行模型查询的调用处把它接在最后一层。
 */
export function effectiveModel(...layers: (string | null | undefined)[]): string | undefined {
  return layers.find((layer) => !!layer) || undefined;
}

/** 全局层的自动选择预览；缺席（拉取失败或尚未拉取）即退回「算不出」，不阻断其余演算。 */
interface AutoBackendsLayer {
  auto?: AutoBackendsResponse | null;
}

/**
 * 当前配置下真正会执行的视频模型。参考生视频走 r2v 桶、其余生成模式走 i2v，与后端
 * `VIDEO_BUCKET_BY_GENERATION_MODE` 同口径；桶被覆盖时它与默认层不是同一个模型，
 * 故时长 / 分辨率 / 声音档位这些按模型查的能力，以及 model_settings 的分辨率键，都问它。
 * 全层皆空时取该桶的自动选择预览。
 */
export function executingVideoModel(
  value: { videoBackend: string; videoProviderI2V: string; videoProviderR2V: string },
  globals: { video: string; videoI2V: string; videoR2V: string } & AutoBackendsLayer,
  usesReferenceImages?: boolean,
): string {
  const bucket = usesReferenceImages ? value.videoProviderR2V : value.videoProviderI2V;
  const globalBucket = usesReferenceImages ? globals.videoR2V : globals.videoI2V;
  const auto = globals.auto?.[usesReferenceImages ? "r2v" : "i2v"];
  return effectiveModel(bucket, value.videoBackend, globalBucket, globals.video, auto) ?? "";
}

/**
 * 文生图路径真正会执行的图片模型——分镜图与资产图都走该路径，图片分辨率按它存取。
 * 图生图另有自己的执行模型，两者不同时后端查不到该键、回落供应商默认档位。全层皆空时取文生图的
 * 自动选择预览。
 */
export function executingImageModel(
  value: { imageBackendDefault: string; imageBackendT2I: string },
  globals: { image: string; imageT2I: string } & AutoBackendsLayer,
): string {
  return (
    effectiveModel(value.imageBackendT2I, value.imageBackendDefault, globals.imageT2I, globals.image, globals.auto?.t2i) ??
    ""
  );
}

/**
 * 候选拉取失败时的细分区降级：只保留已配置的行、候选只列其当前值。已保存的覆盖在后端仍然
 * 生效，整块隐藏会让用户既看不出实际执行的是哪个模型，也无从清除；未配置的行没有候选可选，
 * 仍不渲染，全部未配置即整块折叠区消失。
 */
export function degradeSubFieldsToSaved<T extends { value: string; options: string[] }>(
  fields: T[],
  hasCandidates: boolean,
): T[] {
  if (hasCandidates) return fields;
  return fields.filter((f) => !!f.value).map((f) => ({ ...f, options: [f.value] }));
}
