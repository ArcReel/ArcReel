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

/** Whether discovery protocols are ComfyUI. Sole predicate, never duplicate literal comparisons throughout UI. */
export function isComfyuiProtocol(format: DiscoveryFormat): boolean {
  return format === "comfyui";
}

/** Whether endpoints are ComfyUI. Like isComfyuiProtocol, literals appear here only once. */
export function isComfyuiEndpoint(endpoint: Pick<EndpointDescriptor, "kind">): boolean {
  return endpoint.kind === "comfyui";
}

/** Whether endpoints are ComfyUI video: these alone retain supported seconds on model rows, written to capability-override
 *  durations keys (docs/adr/0082, 0104). Other endpoint durations come from model entries, never edited in this form. */
export function isComfyuiVideoEndpoint(endpoint: Pick<EndpointDescriptor, "kind" | "media_type">): boolean {
  return isComfyuiEndpoint(endpoint) && endpoint.media_type === "video";
}

/** Connectivity-check target URL previews. */
export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  // ComfyUI lacks /models; probes call /system_stats. Previews must follow probes to identify actual requests.
  if (isComfyuiProtocol(format)) return `${trimmed}/system_stats`;
  if (format === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
}

/** Toggle default: nonimage text/video/audio exclude peers by media_type; images by capability intersections.
 *  Exclusion cleanup triggers only when enabling (false→true); disabling existing defaults (true→false) must not clear
 *  other defaults, or one cancellation deletes sibling slots, such as wildcard ↔ split-edits I2I overlaps.
 *  Unloaded catalogs or unmapped endpoints degrade to single-row toggles, preventing all-undefined endpoint
 *  resolution from grouping and clearing defaults across media types.
 *
 *  endpointToImageCaps comes from endpoint-catalog-store, containing image endpoints only. */
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
  // Nonimage text/video/audio clears same-media_type defaults only when enabling.
  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (r.key === targetKey) return { ...r, is_default: isEnabling };
      if (!isEnabling) return r;
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      return { ...r, is_default: false };
    });
  }
  // Images clear capability-intersection conflicts only when enabling.
  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: isEnabling };
    if (!isEnabling) return r;
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    return imageDefaultsCollide(targetCaps, rowCaps) ? { ...r, is_default: false } : r;
  });
}

/** Whether two image defaults are distinguishable, sharing backend `_check_unique_defaults` predicates.
 *
 *  Empty capabilities mean endpoints declare no abilities; ComfyUI had empty sets before inference landed. They are indistinguishable
 *  from any image default, hence conflicting: two defaults yield two default-model rows. Divergent predicates would let UI
 *  admit configurations rejected only on saving. */
function imageDefaultsCollide(a: ImageCap[], b: ImageCap[]): boolean {
  if (a.length === 0 || b.length === 0) return true;
  const bCaps = new Set(b);
  return a.some((c) => bCaps.has(c));
}

/** Default slots occupied by model rows: nonimage uses media_type itself; images use capabilities
 *  prefixed `image:`. Unknown/discontinued endpoints or unloaded catalogs yield
 *  empty sets without exclusion, matching backend _check_unique_defaults skipping unknown endpoints. */
function defaultSlotsFor(
  endpoint: EndpointKey,
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined>,
): string[] {
  const media = endpointToMediaType[endpoint];
  if (media === undefined) return [];
  if (media !== "image") return [media];
  // Empty-capability images occupy no slots here: only ComfyUI can have empty sets, and that protocol lacks discovery,
  // so this merge path cannot reach them. toggleDefaultReducer and servers enforce exclusion separately.
  return (endpointToImageCaps[endpoint] ?? []).map((c) => `image:${c}`);
}

export type MergeRow = { model_id: string; endpoint: EndpointKey; is_default: boolean };

/** Merge checked Fetch models rows into creation forms, resolving default conflicts.
 *
 *  Empty forms return new rows unchanged; callers already mark one default per media type. Otherwise merge in incoming order: existing
 *  matching model IDs retain rows, including edited defaults/multipliers; append others. Empty-ID manual rows never enter Map
 *  deduplication, whose key collisions silently lose rows; retain them separately at the end in original order.
 *
 *  Existing defaults occupy slots first; incoming rows hitting occupied slots yield (is_default→false), while empty slots allow new defaults.
 *  Otherwise backend _check_unique_defaults rejects saves with default_model_conflict. Unknown endpoints/unloaded catalogs
 *  neither occupy nor yield slots. */
export function mergeDiscoveredModels<T extends MergeRow>(
  prev: T[],
  discovered: T[],
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined> = {},
): T[] {
  if (prev.length === 0) {
    return discovered.map((row) => ({ ...row }));
  }

  // Manual rows with empty model_id collide in Maps, overwriting each other and silently losing rows;
  // retain separately, appending in original order.
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
  // Retain existing rows not replaced by discovery responses, including empty-ID manual rows.
  for (const r of remaining.values()) {
    merged.push({ row: r, fromExisting: true });
  }
  for (const r of emptyIdRows) {
    merged.push({ row: r, fromExisting: true });
  }

  // Existing DB-validated prev rows occupy slots first without internal conflicts; discovered rows follow, yielding on collisions.
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
// Model-level capability overrides, initially last_frame only
// ---------------------------------------------------------------------------

/** Sole sparse-override writer: next === undefined restores Follow decisions, removing keys
 *  rather than writing false; backend absence and explicit false differ (follow versus force-off). Empty dictionaries
 *  normalize to null after removal, preventing {} as another equivalent override-absence representation. */
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

/** Sole durations override writer, edited only by ComfyUI video rows: undefined removes keys, restoring
 *  native workflow durations; empty dictionaries normalize to null like withLastFrameOverride. */
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
