import type { EndpointKey, ImageCap, MediaType } from "@/types";

export type DiscoveryFormat = "openai" | "google";
export type ModelLike = { key: string; endpoint: EndpointKey; is_default: boolean };

/** 价格行标签 —— mediaType 由调用方从 endpoint-catalog-store 读出注入。 */
export function priceLabel(
  endpoint: EndpointKey,
  endpointToMediaType: Record<string, MediaType>,
  t: (key: string) => string,
): { input: string; output: string } {
  const media = endpointToMediaType[endpoint];
  if (media === "video") return { input: t("price_per_second"), output: "" };
  if (media === "image") return { input: t("price_per_image"), output: "" };
  if (media === "audio") return { input: t("price_per_10k_chars"), output: "" };
  return { input: t("price_per_m_input"), output: t("price_per_m_output") };
}

/** /models URL 预览。 */
export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
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
    const overlap = rowCaps.some((c) => targetCaps.includes(c));
    return overlap ? { ...r, is_default: false } : r;
  });
}

/** 一行模型占用的「默认槽位」集合：非 image → media_type 自身；image → 各 capability
 *  前缀 `image:`。endpoint 不在 catalog（如 anthropic-messages）或 catalog 未加载 →
 *  空集，该行不参与互斥（与后端 _check_unique_defaults 对未知 endpoint 跳过校验一致）。 */
function defaultSlotsFor(
  endpoint: EndpointKey,
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined>,
): string[] {
  const media = endpointToMediaType[endpoint];
  if (media === undefined) return [];
  if (media !== "image") return [media];
  return (endpointToImageCaps[endpoint] ?? []).map((c) => `image:${c}`);
}

export type MergeRow = { model_id: string; endpoint: EndpointKey; is_default: boolean };

/** 合并「获取模型」结果到当前表单行，并消解默认冲突。
 *
 *  create 模式（prev 为空）：直接返回 discovery 响应的浅拷贝，不做消解——无既存默认需
 *  保护，且发现接口对每个 media_type 至多标一个默认（见后端 discovery _build_result_list），
 *  响应本身无冲突。保持「create 模式原样透传 discovery」的约束。
 *
 *  合并（编辑模式）：按 discovered 顺序保留已有行（含其 is_default 与价格等编辑态），新发现行
 *  追加在对应位置，未被发现响应覆盖的既存行保留在末尾。未填 model_id 的手动行（model_id 同为
 *  空串）不进 Map 去重，否则键冲突会静默丢行；单独按原顺序补在末尾。
 *
 *  默认消解：编辑模式下若某槽位已有用户既存默认，朴素合并会得到「同一 media_type 两个默认」，
 *  保存时被后端 _check_unique_defaults 拒绝（default_model_conflict）。故合并后做一次消解：
 *  既存行优先占据其槽位，新发现行命中已占槽位时让出（is_default→false）；空槽位仍允许新发现行
 *  补默认。endpoint 未知 / catalog 未加载的行不占槽也不让出。 */
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
