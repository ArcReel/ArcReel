import {
  COMFYUI_IMAGE_BINDING_KEYS,
  COMFYUI_REQUIRED_BINDING_KEYS,
  COMFYUI_VIDEO_BINDING_KEYS,
  type ComfyuiBindingCandidate,
  type ComfyuiBindingKey,
  type ComfyuiBindingState,
  type ComfyuiBindingTarget,
  type ComfyuiBindings,
  type ComfyuiEndpointDefinition,
  type ComfyuiInferResponse,
  type ComfyuiMatchOrigin,
  type ComfyuiMediaType,
  type ComfyuiWorkflowNode,
} from "@/types";

// 绑定编辑器的读写辅助：从 workflow 读出可手选的落点，把推断结果与用户的选择合成一份待保存的
// 节点绑定，并算出保存门控的原因。校验的唯一职责在服务端，这里只复现它会拒绝的那几条硬闸门，
// 让用户在点保存之前就看到原因。

/** 语义键顺序即绑定表的行顺序；图像端点没有首尾帧与时间轴那四个。 */
export function bindingKeysFor(mediaType: ComfyuiMediaType): readonly ComfyuiBindingKey[] {
  return mediaType === "image" ? COMFYUI_IMAGE_BINDING_KEYS : COMFYUI_VIDEO_BINDING_KEYS;
}

export function isRequiredBindingKey(key: ComfyuiBindingKey): boolean {
  return COMFYUI_REQUIRED_BINDING_KEYS.includes(key);
}

/** 参考图是一条有序列表，其余语义键各自只有一个落点。 */
export function isListBindingKey(key: ComfyuiBindingKey): boolean {
  return key === "reference_images";
}

/** 一行在绑定表里的状态。`required_unbound` 由必填键没有落点派生，不是服务端的推断状态。 */
export type ComfyuiRowStatus =
  | "auto"
  | "manual"
  | "ambiguous"
  | "not_found"
  | "unsupported"
  | "required_unbound";

// ---------------------------------------------------------------------------
// workflow
// ---------------------------------------------------------------------------

export interface ComfyuiNodeEntry {
  id: string;
  classType: string;
  title: string;
  inputs: Record<string, unknown>;
}

/** 节点 id 在 ComfyUI 里是数字串，按数值排序；非数字的排在其后按字典序。 */
function nodeOrder(id: string): [number, string] {
  const digits = /^(\d+)/.exec(id);
  return digits ? [Number(digits[1]), id] : [Number.MAX_SAFE_INTEGER, id];
}

function compareNodeIds(a: string, b: string): number {
  const [an, as] = nodeOrder(a);
  const [bn, bs] = nodeOrder(b);
  return an === bn ? as.localeCompare(bs) : an - bn;
}

function asNode(value: unknown): ComfyuiWorkflowNode | null {
  if (typeof value !== "object" || value === null) return null;
  const node = value as ComfyuiWorkflowNode;
  return typeof node.class_type === "string" ? node : null;
}

/** 读出 workflow 的全部节点，按节点 id 升序——绑定表与手选下拉共用这一个顺序。 */
export function workflowNodes(workflow: Record<string, unknown>): ComfyuiNodeEntry[] {
  const entries: ComfyuiNodeEntry[] = [];
  for (const [id, raw] of Object.entries(workflow)) {
    const node = asNode(raw);
    if (!node) continue;
    entries.push({
      id,
      classType: node.class_type,
      title: typeof node._meta?.title === "string" ? node._meta.title : "",
      inputs: node.inputs ?? {},
    });
  }
  return entries.sort((a, b) => compareNodeIds(a.id, b.id));
}

/** 每个 `class_type` 在 workflow 里出现了几次，按出现次数降序、同次数按名称排序。 */
export function classTypeCounts(nodes: ComfyuiNodeEntry[]): { classType: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const node of nodes) counts.set(node.classType, (counts.get(node.classType) ?? 0) + 1);
  return [...counts]
    .map(([classType, count]) => ({ classType, count }))
    .sort((a, b) => (b.count === a.count ? a.classType.localeCompare(b.classType) : b.count - a.count));
}

/**
 * 一个输入是不是连线。ComfyUI 的连线形如 `[上游节点 id, 输出序号]`；`{"__value__": [...]}`
 * 是生态里给数组字面值加的包装，解包后才是真值，因此绑得上去。
 */
export function isLinkInput(value: unknown): boolean {
  const unwrapped = unwrapValue(value);
  return Array.isArray(unwrapped) && unwrapped.length === 2 && typeof unwrapped[1] === "number";
}

function unwrapValue(value: unknown): unknown {
  if (typeof value === "object" && value !== null && "__value__" in value) {
    return (value).__value__;
  }
  return value;
}

/** 该输入当前的字面值，供手选下拉展示；连线与缺席都得不到值。 */
export function literalInputText(node: ComfyuiNodeEntry, input: string): string {
  const value = unwrapValue(node.inputs[input]);
  if (value === undefined) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}

/**
 * 可手选的落点全集。产物是节点级的（没有 `input`），其余语义键只能落在字面值输入上——
 * 往连线上填值会被上游覆盖，服务端的校验器直接拒。
 */
export function manualTargets(nodes: ComfyuiNodeEntry[], key: ComfyuiBindingKey): ComfyuiBindingTarget[] {
  const targets: ComfyuiBindingTarget[] = [];
  for (const node of nodes) {
    if (key === "output") {
      targets.push(withTitle({ node: node.id, class_type: node.classType }, node.title));
      continue;
    }
    for (const [input, value] of Object.entries(node.inputs)) {
      if (isLinkInput(value)) continue;
      const target: ComfyuiBindingTarget = { node: node.id, input, class_type: node.classType };
      if (key === "fps") target.direction = "read";
      targets.push(withTitle(target, node.title));
    }
  }
  return targets;
}

function withTitle(target: ComfyuiBindingTarget, title: string): ComfyuiBindingTarget {
  return title ? { ...target, title } : target;
}

/**
 * 有序列表键的勾选与取消。选中项按候选列表的次序排（推断侧的并列次序就是节点序），
 * 手选进来、不在候选列表里的条目排在其后并保持加入顺序——列表顺序即参考图序号。
 */
export function toggleListTarget(
  candidates: ComfyuiBindingCandidate[],
  current: ComfyuiBindingTarget[],
  target: ComfyuiBindingTarget,
): ComfyuiBindingTarget[] {
  const next = current.some((entry) => sameTarget(entry, target))
    ? current.filter((entry) => !sameTarget(entry, target))
    : [...current, target];
  const rank = (entry: ComfyuiBindingTarget): number => {
    const index = candidates.findIndex((candidate) => sameTarget(candidate.target, entry));
    return index < 0 ? candidates.length : index;
  };
  return next
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => rank(a.entry) - rank(b.entry) || a.index - b.index)
    .map((item) => item.entry);
}

/** 两条目标指向同一个落点：产物比节点，其余比节点加字段。 */
export function sameTarget(a: ComfyuiBindingTarget, b: ComfyuiBindingTarget): boolean {
  return a.node === b.node && (a.input ?? null) === (b.input ?? null);
}

/** 一条目标写进 workflow 的哪一格；只读的 `fps` 不写值，因此不占格子。 */
function targetLanding(target: ComfyuiBindingTarget): string | null {
  if (target.direction === "read") return null;
  return target.input === undefined ? `#${target.node}` : `#${target.node}.${target.input}`;
}

// ---------------------------------------------------------------------------
// 推断结果 → 待保存的节点绑定
// ---------------------------------------------------------------------------

/**
 * 推断结果的落盘形态：只有「唯一最高分」的键会自动带上条目，歧义与待确认一条也不带——
 * 那正是要用户来定的。显式不支持沿用它的空列表，其余键缺席（从未推断）。
 */
export function bindingsFromInference(
  inference: ComfyuiInferResponse,
  mediaType: ComfyuiMediaType,
): ComfyuiBindings {
  const bindings: ComfyuiBindings = {};
  for (const key of bindingKeysFor(mediaType)) {
    const result = inference.bindings[key];
    if (!result) continue;
    if (result.state === "unsupported") {
      bindings[key] = [];
      continue;
    }
    if (result.state !== "auto_selected") continue;
    const selected = result.candidates.filter((candidate) => candidate.selected).map((c) => c.target);
    if (selected.length > 0) bindings[key] = selected;
  }
  return bindings;
}

/** 媒体类型改了之后，越界的语义键连同它的条目一起摘掉——它本就不会被填值。 */
export function pruneBindings(bindings: ComfyuiBindings, mediaType: ComfyuiMediaType): ComfyuiBindings {
  const allowed = new Set<string>(bindingKeysFor(mediaType));
  const next: ComfyuiBindings = {};
  for (const [key, targets] of Object.entries(bindings)) {
    if (allowed.has(key)) next[key as ComfyuiBindingKey] = targets;
  }
  return next;
}

/** 该键当前选中的条目在候选列表里的下标集合。 */
export function selectedCandidateIndexes(
  candidates: ComfyuiBindingCandidate[],
  targets: ComfyuiBindingTarget[] | undefined,
): Set<number> {
  const chosen = new Set<number>();
  if (!targets) return chosen;
  candidates.forEach((candidate, index) => {
    if (targets.some((target) => sameTarget(target, candidate.target))) chosen.add(index);
  });
  return chosen;
}

/**
 * 一行的状态。用户本轮改过、或条目来自已保存的定义（沿用与重匹配都是），都算手动指定——
 * 按 `docs/adr/0082`，保存下来的每一条节点绑定本来就是用户确认过的。
 */
export function rowStatus(
  key: ComfyuiBindingKey,
  state: ComfyuiBindingState | undefined,
  targets: ComfyuiBindingTarget[] | undefined,
  options: { touched: boolean; candidates: ComfyuiBindingCandidate[] },
): ComfyuiRowStatus {
  if (targets !== undefined && targets.length === 0) {
    return isRequiredBindingKey(key) ? "required_unbound" : "unsupported";
  }
  if (targets === undefined) {
    // 并列候选优先于「必填未绑定」：有候选摆在那里时，该做的事是选一个，不是从头找。
    if (state === "ambiguous" || state === "needs_confirmation") return "ambiguous";
    return isRequiredBindingKey(key) ? "required_unbound" : "not_found";
  }
  if (options.touched) return "manual";
  const origins = boundOrigins(options.candidates, targets);
  return origins.length > 0 && origins.every((origin) => origin === "inferred") ? "auto" : "manual";
}

/** 这一行的条目各自是怎么来的；手选进来、不在候选列表里的条目不在其中。 */
function boundOrigins(
  candidates: ComfyuiBindingCandidate[],
  targets: ComfyuiBindingTarget[],
): ComfyuiMatchOrigin[] {
  return candidates
    .filter((candidate) => targets.some((target) => sameTarget(target, candidate.target)))
    .map((candidate) => candidate.origin);
}

/**
 * 重导入在这一行留下的记号：只要有一条是按类型加标题迁移过来的就报「已重匹配」——它是更需要
 * 用户过目的那一种；全都原样沿用时报「沿用」；本轮新推断或手选的行没有记号。
 */
export function rowOrigin(
  candidates: ComfyuiBindingCandidate[],
  targets: ComfyuiBindingTarget[] | undefined,
): ComfyuiMatchOrigin | null {
  if (targets === undefined || targets.length === 0) return null;
  const origins = boundOrigins(candidates, targets);
  if (origins.includes("rematched")) return "rematched";
  return origins.length > 0 && origins.every((origin) => origin === "kept") ? "kept" : null;
}

/** 表头右侧的四类计数。 */
export function statusTally(statuses: ComfyuiRowStatus[]): {
  bound: number;
  ambiguous: number;
  notFound: number;
  unsupported: number;
} {
  return {
    bound: statuses.filter((s) => s === "auto" || s === "manual").length,
    ambiguous: statuses.filter((s) => s === "ambiguous").length,
    notFound: statuses.filter((s) => s === "not_found" || s === "required_unbound").length,
    unsupported: statuses.filter((s) => s === "unsupported").length,
  };
}

// ---------------------------------------------------------------------------
// 保存门控
// ---------------------------------------------------------------------------

/** 一条挡住保存的原因：`key` 用来渲染语义键名，`code` 决定说哪一句。 */
export interface ComfyuiSaveBlocker {
  code: "required_unbound" | "needs_choice" | "needs_binding" | "target_taken" | "placeholder_name";
  key?: ComfyuiBindingKey;
  /** `target_taken` 专属：撞在同一格子上的另一个语义键。 */
  otherKey?: ComfyuiBindingKey;
}

/**
 * 保存门控的原因清单，空表即可以保存。三条都是服务端校验器的硬闸门：提示词与产物必须有着落、
 * 并列候选必须有人来定、两个语义键不能写同一个字段；另加一条改名——自动包装的 `meta` 是占位值，
 * 两份不相干的 workflow 会因同作者同名被判成重复。
 */
export function saveBlockers(
  bindings: ComfyuiBindings,
  inference: ComfyuiInferResponse,
  definition: ComfyuiEndpointDefinition,
  placeholderName: string,
): ComfyuiSaveBlocker[] {
  const blockers: ComfyuiSaveBlocker[] = [];
  const name = definition.meta.name.trim();
  if (name === "" || name === placeholderName) blockers.push({ code: "placeholder_name" });
  for (const key of bindingKeysFor(definition.media_type)) {
    const targets = bindings[key];
    if (isRequiredBindingKey(key) && (targets === undefined || targets.length === 0)) {
      blockers.push({ code: "required_unbound", key });
      continue;
    }
    if (targets !== undefined) continue;
    const result = inference.bindings[key];
    if (result === undefined) continue;
    if (result.state !== "ambiguous" && result.state !== "needs_confirmation") continue;
    // 重导入把条目判丢、重跑推断又一无所获时，这一行一个候选也没有：该做的是手选或标为不支持。
    blockers.push({ code: result.candidates.length > 0 ? "needs_choice" : "needs_binding", key });
  }
  const seen = new Map<string, ComfyuiBindingKey>();
  for (const key of bindingKeysFor(definition.media_type)) {
    for (const target of bindings[key] ?? []) {
      const landing = targetLanding(target);
      if (landing === null) continue;
      const taken = seen.get(landing);
      if (taken !== undefined) blockers.push({ code: "target_taken", key, otherKey: taken });
      else seen.set(landing, key);
    }
  }
  return blockers;
}
