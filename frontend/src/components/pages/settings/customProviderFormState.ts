import { uid } from "@/utils/id";
import { compactRangeFormat, parseDurationInput } from "@/utils/duration_format";
import type {
  CapabilityOverrides,
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointDescriptor,
  EndpointKey,
  MatchedCatalogEntry,
  PathOverrides,
  ProviderMultiplierInfo,
  VideoCapabilityFlags,
} from "@/types";
import {
  capabilityFieldsFor,
  isComfyuiEndpoint,
  pathOverridesFor,
  withDurationsOverride,
  type DiscoveryFormat,
} from "./customProviderHelpers";

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
  resolution: string; // 空串 = null
  // 可选时长的原始文本，只有 ComfyUI 视频行可编辑：提交时写回能力覆盖的 durations 键，空串 = 去掉该键、
  // 跟随 workflow 原生时长。加载时取自覆盖，其余行只是原样带着，提交时不读它。
  durations_text: string;
  // 供应商模型倍率的原始文本，提交前 parse；必须是非负有限数。
  multiplier_text: string;
  // 条目绑定：本表单不编辑，只原样回传，否则整表 PUT 会把它清掉。
  entry_binding: string | null;
  // 服务端按 (model_id, 媒体类型, 条目绑定) 对应到的条目；null = 对应不上（未定价），
  // undefined = 还不知道（手动新增、尚未保存的行）。
  matched_entry: MatchedCatalogEntry | null | undefined;
  capability_overrides: CapabilityOverrides | null;
  // 请求地址修改（请求名 → 路径）；null = 全部按端点默认。
  path_overrides: PathOverrides | null;
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
  original_path_overrides: PathOverrides | null;
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
  /** 供应商倍率；空串 = 默认值（保存时提交 null）。 */
  providerMultiplier: string;
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
    resolution: "",
    durations_text: "",
    multiplier_text: "1",
    entry_binding: null,
    matched_entry: undefined,
    capability_overrides: null,
    path_overrides: null,
    system_capabilities: null,
    global_bucket_refs: [],
    ...partial,
  };
  return {
    ...base,
    original_model_id: base.model_id,
    original_endpoint: base.endpoint,
    original_capability_overrides: base.capability_overrides,
    original_path_overrides: base.path_overrides,
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
    // 发现结果里的条目对应与保存后服务端算的是同一条判据；没带这一项时按「还不知道」处理。
    matched_entry: m.matched_entry,
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
    resolution: m.resolution ?? "",
    durations_text: m.capability_overrides?.durations?.length ? compactRangeFormat(m.capability_overrides.durations) : "",
    multiplier_text: String(m.multiplier),
    entry_binding: m.entry_binding,
    matched_entry: m.matched_entry,
    capability_overrides: m.capability_overrides,
    path_overrides: m.path_overrides,
    system_capabilities: m.system_capabilities,
    global_bucket_refs: m.global_bucket_refs ?? [],
  });
}

// 并发上限：number 输入用受控字符串存储；空串 = 未设置（null，走全局默认）。
function workersToStr(n?: number | null): string {
  return n != null ? String(n) : "";
}

/** 自定义供应商没有设置倍率时的值；新建表单还取不到服务端的默认值，按它显示。 */
export const DEFAULT_PROVIDER_MULTIPLIER: ProviderMultiplierInfo = { multiplier: 1, default: 1 };

/**
 * 新建供应商还没有 id、取不到服务端的默认倍率，按协议给出同一份默认值：ComfyUI 协议为 0，其余为 1
 * （与 lib/billing/multipliers.py 的 default_provider_multiplier 一致）。
 */
export function newProviderDefaultMultiplier(isComfyui: boolean): number {
  return isComfyui ? 0 : DEFAULT_PROVIDER_MULTIPLIER.default;
}

// 供应商倍率：等于默认值时显示为空，表示「跟随默认」；保存空串即恢复默认。
function providerMultiplierToStr(info: ProviderMultiplierInfo): string {
  return info.multiplier === info.default ? "" : String(info.multiplier);
}

/** 已保存的供应商对应的表单内容。 */
export function formFromProvider(
  provider: CustomProviderInfo,
  multiplier: ProviderMultiplierInfo = DEFAULT_PROVIDER_MULTIPLIER,
): CustomProviderFormValue {
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
    providerMultiplier: providerMultiplierToStr(multiplier),
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
    providerMultiplier: "",
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

// 倍率：非负有限数。只认十进制写法（「1.5」「0.8」「.5」），科学计数、负号与其他字符一律
// 视为非法，返回 undefined 由保存拦截——不用 parseFloat 静默截断（「1.5x」→1.5）。
const DECIMAL_RE = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

/** 模型倍率：必填。合法时返回数值，空串或非法输入返回 undefined。 */
export function parseMultiplier(s: string): number | undefined {
  const trimmed = s.trim();
  if (!DECIMAL_RE.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

/** 供应商倍率：空串 = 恢复默认（null）；否则同 parseMultiplier，非法输入返回 undefined。 */
export function parseOptionalMultiplier(s: string): number | null | undefined {
  return s.trim() ? parseMultiplier(s) : null;
}

/**
 * 行在当前编辑下对应到的条目。条目由服务端按模型 ID 与端点的媒体类型对应，改过这两项的行
 * 对应结果要重新算，前端不猜，按「还不知道」（undefined）处理；改回原值即取回加载时的结果。
 */
export function matchedEntryFor(row: ModelRow): MatchedCatalogEntry | null | undefined {
  return row.model_id === row.original_model_id && row.endpoint === row.original_endpoint ? row.matched_entry : undefined;
}

export interface RowToInputOptions {
  /** 这一行的可选时长可编辑（ComfyUI 视频端点、档位给得出来）：此时由 durations_text 写回覆盖。 */
  durationsEditable: boolean;
}

export function rowToInput(r: ModelRow, { durationsEditable }: RowToInputOptions): CustomProviderModelInput {
  let overrides = r.capability_overrides;
  if (durationsEditable) {
    // 失败时直接抛 DurationParseError；保存在调用前应已拦截，故此处只负责诚实地把字符串
    // 转成 list[int] 而不静默降级（无效输入被当成空串，会把用户的档位悄悄换回原生时长）
    overrides = withDurationsOverride(overrides, parseDurationInput(r.durations_text) ?? undefined);
  }
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    // 其余行的覆盖（含升级迁移写入的 durations、max_output_tokens）原样回传：整表 PUT 省略即清空
    capability_overrides: overrides,
    path_overrides: r.path_overrides,
    // 保存前已拦截非法倍率，这里的回退值不会生效
    multiplier: parseMultiplier(r.multiplier_text) ?? 1,
    entry_binding: r.entry_binding,
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
    return {
      ...row,
      endpoint,
      is_default: false,
      ...capabilityFieldsFor(row, row.model_id, endpoint),
      path_overrides: pathOverridesFor(row, endpoint),
    };
  });
}
