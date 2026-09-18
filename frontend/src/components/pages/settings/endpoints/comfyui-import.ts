import type {
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  CustomEndpointInfo,
} from "@/types";

/**
 * 一份刚导入、还没保存的 ComfyUI 端点：定义连同它这一轮的推断结果。
 *
 * 全新导入时 `record` 为 null；重新导入到某个已保存的端点上时它指向那个端点，保存走整份替换、
 * 键与模型行挂接不变。
 */
export interface ComfyuiImportDraft {
  /**
   * 这一次导入的一次性身份，每产生一份草稿换一个值。
   *
   * 详情组件把定义、绑定与推断结果都收在自己的 `useState` 里，只在挂载那一刻取自 props；重新
   * 导入保留端点键与选中项，不换实例的话屏幕上还是旧 workflow、保存下去的也是旧的。详情的
   * React key 带上它，换一份草稿即换一个实例。
   */
  token: string;
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  /** 这份定义是从哪个文件来的，详情头部据此显示来源。 */
  fileName: string;
  inference: ComfyuiInferResponse;
}

/**
 * 把新导入的这份载荷接到既有端点上：换掉 workflow，身份、媒体类型、凭据与已确认的节点绑定
 * 一律沿用原端点——重匹配的输入正是「新 workflow 加它原来那份节点绑定」。
 *
 * 载荷本身就是一份 ComfyUI 端点定义时不做这一步：那是用户拿一整份定义覆盖旧的，它自己带着
 * 名字、媒体类型与节点绑定。
 */
export function reimportedDefinition(
  existing: ComfyuiEndpointDefinition,
  incoming: ComfyuiEndpointDefinition,
  incomingIsDefinition: boolean,
): ComfyuiEndpointDefinition {
  if (incomingIsDefinition) return incoming;
  return { ...existing, workflow: incoming.workflow };
}

/** 一份定义是不是 ComfyUI 端点定义。 */
export function isComfyuiDefinition(value: unknown): value is ComfyuiEndpointDefinition {
  return typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "comfyui";
}
