import type {
  CapabilityOverrides,
  DiscoveryFormat,
  EndpointDescriptor,
  EndpointKey,
  ImageCap,
  MediaType,
} from "@/types";

export type { DiscoveryFormat };
export type ModelLike = { key: string; endpoint: EndpointKey; is_default: boolean };

/** 模型发现协议是否为 ComfyUI。判据只此一处，界面各处不各写一遍字面量比较。 */
export function isComfyuiProtocol(format: DiscoveryFormat): boolean {
  return format === "comfyui";
}

/** 一个调用端点是否为 ComfyUI 端点。与 isComfyuiProtocol 同理：字面量只在这里出现一次。 */
export function isComfyuiEndpoint(endpoint: Pick<EndpointDescriptor, "kind">): boolean {
  return endpoint.kind === "comfyui";
}

/** 一个调用端点是否为 ComfyUI 视频端点：模型行上只有这种端点还留着「支持秒数」，写进能力覆盖的
 *  durations 键（docs/adr/0082、0104）。其余端点的可选时长由模型条目决定，本表单不编辑。 */
export function isComfyuiVideoEndpoint(endpoint: Pick<EndpointDescriptor, "kind" | "media_type">): boolean {
  return isComfyuiEndpoint(endpoint) && endpoint.media_type === "video";
}

/** 连通性检查将打到的 URL 预览。 */
export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  // ComfyUI 没有 /models，探针打的是 /system_stats；预览跟着探针走才有指认价值。
  if (isComfyuiProtocol(format)) return `${trimmed}/system_stats`;
  if (format === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
}

/** 切 default：非 image 媒体类型（text/video/audio）同 media_type 内互斥；image 按 capability 集合交集互斥。
 *  互斥清理仅在 enabling（false→true）时触发——取消已有默认（true→false）时不应连带清掉
 *  其他默认项，否则一次"取消"会误删兄弟槽位（如 wildcard ↔ split-edits 的 I2I 重叠）。
 *  catalog 未加载或 endpoint 不在映射内时降级为「单行 toggle」——避免所有 endpoint
 *  都解析成 undefined 时被当作同组，误清掉其他媒体类型的默认项。
 *
 *  endpointToImageCaps：来自 endpoint-catalog-store，仅 image endpoint 有条目。 */
export function toggleDefaultReducer<T extends ModelLike>(
  rows: T[],
  targetKey: string,
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined> = {},
): T[] {
  const target = rows.find((r) => r.key === targetKey);
  if (!target) return rows;
  const isEnabling = !target.is_default;
  const targetMedia = endpointToMediaType[target.endpoint];
  if (targetMedia === undefined) {
    return rows.map((r) => (r.key === targetKey ? { ...r, is_default: isEnabling } : r));
  }
  // 非 image（text/video/audio）：仅 enabling 时清同 media_type 其他默认
  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (r.key === targetKey) return { ...r, is_default: isEnabling };
      if (!isEnabling) return r;
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      return { ...r, is_default: false };
    });
  }
  // image：仅 enabling 时按 capability 交集清冲突
  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: isEnabling };
    if (!isEnabling) return r;
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    return imageDefaultsCollide(targetCaps, rowCaps) ? { ...r, is_default: false } : r;
  });
}

/** 两个 image 默认分不分得开。与后端 `_check_unique_defaults` 同一条判据。
 *
 *  能力集为空即「这个端点没声明它能做什么」——ComfyUI 端点在能力推导落地前都是空集。它与任何一个
 *  image 默认都分不开，因此算冲突：放两个进去，取默认模型时会一次查出两行。两边判据若不一致，界面
 *  会放行一份保存时才被拒的配置。 */
function imageDefaultsCollide(a: ImageCap[], b: ImageCap[]): boolean {
  if (a.length === 0 || b.length === 0) return true;
  return a.some((c) => b.includes(c));
}

/** 一行模型占用的「默认槽位」集合：非 image → media_type 自身；image → 各 capability
 *  前缀 `image:`。endpoint 不在 catalog（如已下线的端点）或 catalog 未加载 →
 *  空集，该行不参与互斥（与后端 _check_unique_defaults 对未知 endpoint 跳过校验一致）。 */
function defaultSlotsFor(
  endpoint: EndpointKey,
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined>,
): string[] {
  const media = endpointToMediaType[endpoint];
  if (media === undefined) return [];
  if (media !== "image") return [media];
  // 能力集为空的 image 端点在这里不占槽：只有 ComfyUI 端点会是空集，而该协议没有模型发现，
  // 这条合并路径走不到它。互斥判据在 toggleDefaultReducer 与服务端两处。
  return (endpointToImageCaps[endpoint] ?? []).map((c) => `image:${c}`);
}

export type MergeRow = { model_id: string; endpoint: EndpointKey; is_default: boolean };

/** 把「获取模型」勾选的模型并入新建表单的模型行，并消解默认冲突。
 *
 *  表单还没有行时原样返回新行（调用方已按媒体类型给每类只标一个默认）。否则按新行顺序并入：模型 ID 与已有行
 *  相同的保留已有行（含其默认与倍率等编辑），其余追加；未填模型 ID 的手动行（model_id 同为空串）不进 Map
 *  去重，否则键冲突会静默丢行，单独按原顺序补在末尾。
 *
 *  默认消解：已有行先占据其默认槽位，新行命中已占槽位时让出（is_default→false），空槽位仍允许新行补默认；
 *  否则保存时被后端 _check_unique_defaults 拒绝（default_model_conflict）。endpoint 未知或端点目录未加载的
 *  行不占槽也不让出。 */
export function mergeDiscoveredModels<T extends MergeRow>(
  prev: T[],
  discovered: T[],
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined> = {},
): T[] {
  if (prev.length === 0) {
    return discovered.map((row) => ({ ...row }));
  }

  // 未填 model_id 的手动行 model_id 同为空串，进 Map 会互相覆盖（键唯一）导致静默丢行；
  // 单独留存，按原顺序补在末尾。
  const remaining = new Map<string, T>();
  const emptyIdRows: T[] = [];
  for (const r of prev) {
    const key = r.model_id.trim();
    if (key) remaining.set(key, r);
    else emptyIdRows.push(r);
  }

  const merged: { row: T; fromExisting: boolean }[] = [];
  for (const d of discovered) {
    const key = d.model_id.trim();
    const existing = key ? remaining.get(key) : undefined;
    if (existing) {
      merged.push({ row: existing, fromExisting: true });
      remaining.delete(key);
    } else {
      merged.push({ row: d, fromExisting: false });
    }
  }
  // 保留未被发现响应覆盖的既存行（含未填 model_id 的手动行）
  for (const r of remaining.values()) {
    merged.push({ row: r, fromExisting: true });
  }
  for (const r of emptyIdRows) {
    merged.push({ row: r, fromExisting: true });
  }

  // 既存行先占槽（prev 来自已通过后端校验的 DB，内部无冲突），新发现行后处理、命中即让出
  const claimed = new Set<string>();
  const out = merged.map(({ row }) => ({ ...row }));
  const existingIndices: number[] = [];
  const newIndices: number[] = [];
  merged.forEach((item, i) => {
    if (item.fromExisting) existingIndices.push(i);
    else newIndices.push(i);
  });
  for (const i of [...existingIndices, ...newIndices]) {
    const row = out[i];
    if (!row.is_default) continue;
    const slots = defaultSlotsFor(row.endpoint, endpointToMediaType, endpointToImageCaps);
    if (slots.some((s) => claimed.has(s))) {
      row.is_default = false;
    } else {
      for (const s of slots) claimed.add(s);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// 能力覆盖（模型级，首批仅 last_frame）
// ---------------------------------------------------------------------------

/** 稀疏覆盖字典的唯一写入点：next === undefined 表示回到「跟随判定」，此时把该键从字典移除
 *  而不是写 false——键缺席与显式 false 在后端是两种语义（跟随判定 vs 强制关）。移除后字典为空
 *  则整体收敛回 null，避免把 {} 存进去让「有无覆盖」多出一种等价表示。 */
export function withLastFrameOverride(
  prev: CapabilityOverrides | null,
  next: boolean | undefined,
): CapabilityOverrides | null {
  const merged: CapabilityOverrides = { ...(prev ?? {}) };
  if (next === undefined) {
    delete merged.last_frame;
  } else {
    merged.last_frame = next;
  }
  return Object.keys(merged).length > 0 ? merged : null;
}

/** 稀疏覆盖字典里 durations 键的唯一写入点（只有 ComfyUI 视频行编辑它）：undefined 即移除该键，回到跟随
 *  workflow 的原生时长；移除后为空则与 withLastFrameOverride 一样收敛回 null。 */
export function withDurationsOverride(
  prev: CapabilityOverrides | null,
  durations: number[] | undefined,
): CapabilityOverrides | null {
  const merged: CapabilityOverrides = { ...(prev ?? {}) };
  if (durations === undefined) {
    delete merged.durations;
  } else {
    merged.durations = durations;
  }
  return Object.keys(merged).length > 0 ? merged : null;
}
