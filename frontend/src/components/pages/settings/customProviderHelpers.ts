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

export function isComfyuiProtocol(format: DiscoveryFormat): boolean {
  return format === "comfyui";
}

export function isComfyuiEndpoint(endpoint: Pick<EndpointDescriptor, "kind">): boolean {
  return endpoint.kind === "comfyui";
}

export function isComfyuiVideoEndpoint(endpoint: Pick<EndpointDescriptor, "kind" | "media_type">): boolean {
  return isComfyuiEndpoint(endpoint) && endpoint.media_type === "video";
}

export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (isComfyuiProtocol(format)) return `${trimmed}/system_stats`;
  if (format === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
}

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
  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (r.key === targetKey) return { ...r, is_default: isEnabling };
      if (!isEnabling) return r;
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      return { ...r, is_default: false };
    });
  }
  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: isEnabling };
    if (!isEnabling) return r;
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    return imageDefaultsCollide(targetCaps, rowCaps) ? { ...r, is_default: false } : r;
  });
}

function imageDefaultsCollide(a: ImageCap[], b: ImageCap[]): boolean {
  if (a.length === 0 || b.length === 0) return true;
  const bCaps = new Set(b);
  return a.some((c) => bCaps.has(c));
}

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

export function mergeDiscoveredModels<T extends MergeRow>(
  prev: T[],
  discovered: T[],
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined> = {},
): T[] {
  if (prev.length === 0) {
    return discovered.map((row) => ({ ...row }));
  }

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
  for (const r of remaining.values()) {
    merged.push({ row: r, fromExisting: true });
  }
  for (const r of emptyIdRows) {
    merged.push({ row: r, fromExisting: true });
  }

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
