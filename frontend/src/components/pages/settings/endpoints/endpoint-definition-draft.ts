import type {
  EndpointDefinition,
  EndpointExtractSpec,
  EndpointInputEncoding,
  EndpointInputSource,
  EndpointPathItem,
  EndpointStandardStatus,
} from "@/types";

// 定义草稿的读写辅助。表单与 JSON 视图共享同一份 EndpointDefinition 对象，
// 这里只提供「读出 UI 需要的形状」与「写回时保留未知字段」的转换，不做校验——
// 校验是服务端 POST /custom-endpoints/validate 的唯一职责。

export const INPUT_SOURCES: EndpointInputSource[] = [
  "start_image",
  "end_image",
  "reference_images",
  "reference_audio_files",
];

export const INPUT_ENCODINGS: EndpointInputEncoding[] = ["data_uri", "base64"];

export const STANDARD_STATUSES: EndpointStandardStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
];

export const HTTP_METHODS = ["GET", "POST", "PUT"];

/** 表单分节 id，同时是诊断卡定位与竖轨编号的依据。 */
export type EndpointFormSection =
  | "meta"
  | "auth"
  | "inputs"
  | "submit"
  | "poll"
  | "status"
  | "capabilities"
  | "test";

/** 新建定义的预填：方法与恒等状态映射有通用答案，能力与取值路径各家差异大，留空。 */
export function newEndpointDefinition(author: string): EndpointDefinition {
  return {
    kind: "declarative",
    schema_version: "1.1.0",
    meta: { name: "", author, version: "1.0.0" },
    auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
    inputs: { first_frame: { source: "start_image", encoding: "data_uri" } },
    submit: {
      method: "POST",
      url: "{{ base_url }}/",
      body: {},
      extract: { task_id: [] },
    },
    poll: {
      method: "GET",
      url: "{{ base_url }}/",
      extract: { status: [], video_url: [] },
    },
    status_map: {
      queued: "queued",
      processing: "running",
      succeeded: "succeeded",
      failed: "failed",
    },
  };
}

// ---------------------------------------------------------------------------
// 取值路径
// ---------------------------------------------------------------------------

/** 读出取值声明中的路径数组；两种简写/全写形态归一，缺席为空数组。 */
export function readPaths(spec: EndpointExtractSpec | undefined): EndpointPathItem[] {
  if (spec === undefined) return [];
  if (Array.isArray(spec)) return spec;
  return spec.paths ?? [];
}

/** 写回路径数组，保留原形态的 accept；空数组写成空简写而非删键，避免字段静默消失。 */
export function writePaths(
  spec: EndpointExtractSpec | undefined,
  paths: EndpointPathItem[],
): EndpointExtractSpec {
  if (spec !== undefined && !Array.isArray(spec)) {
    return spec.accept === undefined ? { paths } : { paths, accept: spec.accept };
  }
  return paths;
}

/** 路径项是否可在表单里直接编辑；json_decode 形态只在 JSON 视图编辑。 */
export function isPlainPath(item: EndpointPathItem): item is string {
  return typeof item === "string";
}

export function pathItemText(item: EndpointPathItem): string {
  return isPlainPath(item) ? item : item.path;
}

// ---------------------------------------------------------------------------
// 诊断定位
// ---------------------------------------------------------------------------

const SECTION_BY_ROOT: Record<string, EndpointFormSection> = {
  meta: "meta",
  auth: "auth",
  inputs: "inputs",
  submit: "submit",
  poll: "poll",
  result: "poll",
  status_map: "status",
  capabilities: "capabilities",
};

/**
 * 把诊断的定位串（如 `poll.extract.video_url[0]`）映射到所属表单分节。
 * `enum_maps`、`defaults`、`schema_version` 这类字段表单里没有控件，返回 null
 * 表示只能在 JSON 视图处理——归给某个分节会把用户送到一个找不到该字段的地方。
 */
export function sectionOfIssuePath(path: string): EndpointFormSection | null {
  const root = path.replace(/^\$\.?/, "").split(/[.[]/, 1)[0];
  return SECTION_BY_ROOT[root] ?? null;
}

// ---------------------------------------------------------------------------
// 不可变写入
// ---------------------------------------------------------------------------

/** 按键序重建对象，让重命名键不改变字段顺序——顺序变化会让 JSON 视图无谓地跳动。 */
export function renameKey<T>(
  record: Record<string, T>,
  from: string,
  to: string,
  value: T,
): Record<string, T> {
  if (from !== to && to in record) return record;
  const next: Record<string, T> = {};
  for (const [k, v] of Object.entries(record)) {
    if (k === from) next[to] = value;
    else next[k] = v;
  }
  if (!(from in record)) next[to] = value;
  return next;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * JSON 视图写回草稿前的结构闸：表单与头部不做防御性访问（草稿即定义），
 * 直接解引用 meta、submit.extract、poll.extract 这几个容器，缺任一即不可渲染。
 * 字段级缺失（如 meta.name）只影响单个控件的取值，交给服务端校验诊断。
 */
export function isRenderableDefinition(value: unknown): value is EndpointDefinition {
  return (
    isRecord(value) &&
    isRecord(value.meta) &&
    isRecord(value.submit) &&
    isRecord(value.submit.extract) &&
    isRecord(value.poll) &&
    isRecord(value.poll.extract)
  );
}

/**
 * 导出文件名，与市场条目 slug 同一规则 `^[a-z0-9][a-z0-9-]{0,63}$`：从 meta.name 去重音、转小写，
 * 其余字符折成连字符，没有可用 ASCII 字符时退化为 `endpoint`。有安装记录的端点直接用记录里的 slug。
 */
export function definitionFileName(definition: EndpointDefinition, installationSlug?: string | null): string {
  if (installationSlug) return `${installationSlug}.json`;
  const slug = (definition.meta?.name ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, 64)
    .replace(/-+$/, "");
  return `${slug || "endpoint"}.json`;
}
