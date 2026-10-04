import { uid } from "@/utils/id";
import { compactRangeFormat, parseDurationInput } from "@/utils/duration_format";
import type {
  CapabilityOverrides,
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointDescriptor,
  EndpointKey,
  VideoCapabilityFlags,
} from "@/types";
import { capabilityFieldsFor, isComfyuiEndpoint, type DiscoveryFormat } from "./customProviderHelpers";

// ---------------------------------------------------------------------------
// 自定义供应商表单的编辑单元内容：已保存的供应商与未保存修改都按这份形状比较。
// ---------------------------------------------------------------------------

export interface ModelRow {
  /** 行的稳定标识：已落库的模型取模型 id，新增行随机生成。 */
  key: string;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string;
  price_input: string;
  price_output: string;
  currency: string;
  resolution: string; // 空串 = null
  supported_durations_text: string; // 用户原始文本，提交前 parse；空串 = 让后端按 preset 兜底
  max_output_tokens_text: string; // 仅文本模型；空串 = 未登记
  capability_overrides: CapabilityOverrides | null;
  // 系统按 (endpoint, model_id) 判定的能力，只读展示用；null = 非视频模型，或该行尚未落库
  // （新增/改过 model_id 的行判定要后端算，前端不猜），此时控件只显示「待判定」。
  system_capabilities: VideoCapabilityFlags | null;
  // 正在引用该模型的全局 system_settings 键名，只读展示用；新增/未落库的行恒为空数组。
  global_bucket_refs: string[];
  // 行创建时的快照，之后不再变化：model_id/endpoint 的清除判断须对齐这份原始值而非上一次
  // 的中间态——逐字符编辑 model_id 时若拿"上一次的值"作基准，第一次改动即清空覆盖，之后就
  // 算把输入改回原值也已丢失、无法通过继续编辑恢复；改回原值时应从这份快照原样取回覆盖。
  original_model_id: string;
  original_endpoint: EndpointKey;
  original_capability_overrides: CapabilityOverrides | null;
  original_system_capabilities: VideoCapabilityFlags | null;
  original_global_bucket_refs: string[];
}

export interface CustomProviderFormValue {
  displayName: string;
  /** 用户选过的协议。「还没选过」与「选了 openai」不是一回事：只有前者才让接线过来的端点定协议。 */
  pickedFormat: DiscoveryFormat | null;
  baseUrl: string;
  /** 新输入的密钥；编辑已有供应商时留空表示保留现有密钥。 */
  apiKey: string;
  /** 「该供应商无需密钥」：本地部署等无凭证接口保存空密钥，同时解除新建时的必填校验。 */
  noApiKey: boolean;
  models: ModelRow[];
  imageMaxWorkers: string;
  videoMaxWorkers: string;
  audioMaxWorkers: string;
}

//: 新模型行默认挂的端点；协议从 ComfyUI 切走时，挂不住的行也退回它。
export const DEFAULT_ENDPOINT = "openai-chat" as EndpointKey;

//: 「这一行还没有端点」。切进 ComfyUI 协议而一个 ComfyUI 端点都还没有时，挂不住的行停在这里：
//: 选择器显示未选择，保存被拦下，直到用户导入端点并为它选一个。
export const UNSET_ENDPOINT = "" as EndpointKey;

export function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  const base = {
    key: uid(),
    model_id: "",
    display_name: "",
    endpoint: DEFAULT_ENDPOINT,
    is_default: false,
    is_enabled: true,
    price_unit: "",
    price_input: "",
    price_output: "",
    currency: "USD",
    resolution: "",
    supported_durations_text: "",
    max_output_tokens_text: "",
    capability_overrides: null,
    system_capabilities: null,
    global_bucket_refs: [],
    ...partial,
  };
  return {
    ...base,
    original_model_id: base.model_id,
    original_endpoint: base.endpoint,
    original_capability_overrides: base.capability_overrides,
    original_system_capabilities: base.system_capabilities,
    original_global_bucket_refs: base.global_bucket_refs,
  };
}

export function discoveredToRow(m: DiscoveredModel): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    max_output_tokens_text: m.max_output_tokens != null ? String(m.max_output_tokens) : "",
  });
}

function existingToRow(m: CustomProviderInfo["models"][number]): ModelRow {
  return newModelRow({
    // 行标识取模型 id：保存后重取的供应商与提交的内容逐行对得上，不会被当成未保存修改
    key: `model-${m.id}`,
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    price_unit: m.price_unit ?? "",
    price_input: m.price_input != null ? String(m.price_input) : "",
    price_output: m.price_output != null ? String(m.price_output) : "",
    currency: m.currency ?? "",
    resolution: m.resolution ?? "",
    supported_durations_text: m.supported_durations ? compactRangeFormat(m.supported_durations) : "",
    max_output_tokens_text: m.max_output_tokens != null ? String(m.max_output_tokens) : "",
    capability_overrides: m.capability_overrides,
    system_capabilities: m.system_capabilities,
    global_bucket_refs: m.global_bucket_refs ?? [],
  });
}

// 并发上限：number 输入用受控字符串存储；空串 = 未设置（null，走全局默认）。
function workersToStr(n?: number | null): string {
  return n != null ? String(n) : "";
}

/** 已保存的供应商对应的表单内容。 */
export function formFromProvider(provider: CustomProviderInfo): CustomProviderFormValue {
  return {
    displayName: provider.display_name,
    pickedFormat: provider.discovery_format,
    baseUrl: provider.base_url,
    apiKey: "",
    noApiKey: false,
    models: provider.models.map(existingToRow),
    imageMaxWorkers: workersToStr(provider.image_max_workers),
    videoMaxWorkers: workersToStr(provider.video_max_workers),
    audioMaxWorkers: workersToStr(provider.audio_max_workers),
  };
}

/** 新建表单的初始内容；从「调用端点」接线过来时预填接口地址，并按所选端点起一行模型。 */
export function newProviderForm(initialBaseUrl?: string, initialEndpoint?: EndpointKey): CustomProviderFormValue {
  return {
    displayName: "",
    pickedFormat: null,
    baseUrl: initialBaseUrl ?? "",
    apiKey: "",
    noApiKey: false,
    models: initialEndpoint ? [newModelRow({ endpoint: initialEndpoint })] : [],
    imageMaxWorkers: "",
    videoMaxWorkers: "",
    audioMaxWorkers: "",
  };
}

// 空串 = 未设置（null）；否则必须是正整数（≥1）。返回 undefined 表示非法
// 输入（0、小数、科学计数、负号、含非数字字符），由保存拦截并提示——不再用 parseInt
// 静默截断（"1.5"→1、"1e3"→1）把非法值写成错误配置。0 不是合法用户输入。
export function parsePositiveInt(s: string): number | null | undefined {
  const trimmed = s.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1 ? n : undefined;
}

export function rowToInput(r: ModelRow): CustomProviderModelInput {
  const trimmed = r.supported_durations_text.trim();
  // 失败时直接抛 DurationParseError；保存在调用前应已拦截，故此处只负责诚实地把字符串
  // 转成 list[int] 而不静默降级（避免无效输入被改成 null 后被后端 preset 自动推断覆盖）
  const supported_durations = trimmed ? parseDurationInput(trimmed) : null;
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.price_unit ? { price_unit: r.price_unit } : {}),
    ...(r.price_input ? { price_input: parseFloat(r.price_input) } : {}),
    ...(r.price_output ? { price_output: parseFloat(r.price_output) } : {}),
    ...(r.currency ? { currency: r.currency } : {}),
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    ...(supported_durations ? { supported_durations } : { supported_durations: null }),
    max_output_tokens: parsePositiveInt(r.max_output_tokens_text) ?? null,
    capability_overrides: r.capability_overrides,
  };
}

/**
 * 行挂不挂得住当前协议是一路派生下来的，不是切协议那一刻改写一遍行就算数：ComfyUI 端点只挂得上
 * ComfyUI 供应商，反之亦然（docs/adr/0081 的双向配对），而这份判断要查端点目录——目录是异步取的，
 * 切协议那一刻它可能还没回来，回来之后也不会有人再重算一遍。留着挂不住的旧值，那一行在端点选择器
 * 里是隐着的（选择器按协议过滤），用户看不见它，保存时才吃一个 422。
 *
 * 改挂的去处：切进 ComfyUI 取第一个 ComfyUI 端点，切走退回新行的默认端点；一个 ComfyUI 端点都
 * 还没有时没有去处，行落在 UNSET_ENDPOINT 上——用户看得见、保存拦得住，比留个隐形的旧端点强。
 * 派生而非改写还带来一点：切走再切回来，原先手选的那个端点自己回来了，models 里存的始终是用户
 * 最后一次显式选择。目录还没回来时（`catalog` 为 null）一行都判不了，原样返回。
 */
export function effectiveModelRows(
  models: ModelRow[],
  catalog: EndpointDescriptor[] | null,
  isComfyui: boolean,
): ModelRow[] {
  if (catalog === null) return models;
  const firstComfyui = catalog.find(isComfyuiEndpoint)?.key;
  return models.map((row) => {
    const descriptor = catalog.find((item) => item.key === row.endpoint);
    if (descriptor !== undefined && isComfyuiEndpoint(descriptor) === isComfyui) return row;
    const endpoint = (isComfyui ? firstComfyui : DEFAULT_ENDPOINT) ?? UNSET_ENDPOINT;
    // 换了一路，默认标记与能力覆盖随之作废：覆盖的合法性本就绑在 (endpoint, model_id) 上，
    // 而 ComfyUI 协议整个关闭覆盖（服务端 _check_protocol_constraints），留着必被拒。
    return { ...row, endpoint, is_default: false, ...capabilityFieldsFor(row, row.model_id, endpoint) };
  });
}
