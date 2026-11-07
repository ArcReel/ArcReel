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

// Binding-editor helpers: read selectable workflow targets, combine inference/user choices into pending
// bindings and derive save-blocking reasons. Servers exclusively validate; reproduce hard refusals only
// so users see causes before saves.

/** Semantic-key order is table order; image endpoints omit the four first/last-frame and timeline keys. */
export function bindingKeysFor(mediaType: ComfyuiMediaType): readonly ComfyuiBindingKey[] {
  return mediaType === "image" ? COMFYUI_IMAGE_BINDING_KEYS : COMFYUI_VIDEO_BINDING_KEYS;
}

export function isRequiredBindingKey(key: ComfyuiBindingKey): boolean {
  return COMFYUI_REQUIRED_BINDING_KEYS.includes(key);
}

/** References are ordered lists; order defines image indices, maintained on selection/deselection. */
export function isListBindingKey(key: ComfyuiBindingKey): boolean {
  return key === "reference_images";
}

/**
 * These keys allow multiple unordered targets: workflow consumers can each own dimensions/frame counts;
 * `fps` allows multiple readonly targets, rejecting conflicting values only. Construction writes each dimension/frame binding and reads each rate,
 * so selecting candidates adds/removes only that target, never replacing other same-key bindings, or other branches
 * stop receiving dimensions/frame counts.
 */
export function isMultiTargetBindingKey(key: ComfyuiBindingKey): boolean {
  return key === "width" || key === "height" || key === "frames" || key === "fps";
}

/** Unordered multitarget toggles remove present targets/append absent targets, preserving others. */
export function toggleSetTarget(
  current: ComfyuiBindingTarget[],
  target: ComfyuiBindingTarget,
): ComfyuiBindingTarget[] {
  return current.some((entry) => sameTarget(entry, target))
    ? current.filter((entry) => !sameTarget(entry, target))
    : [...current, target];
}

/** Binding-row states; `required_unbound` derives missing required targets, never server inference states. */
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

/** ComfyUI node IDs are numeric strings sorted numerically; nonnumeric IDs follow lexically. */
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

/** Read every workflow node by ascending id, shared by binding tables/manual dropdowns. */
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

/** Count each workflow `class_type`, descending by count then name. */
export function classTypeCounts(nodes: ComfyuiNodeEntry[]): { classType: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const node of nodes) counts.set(node.classType, (counts.get(node.classType) ?? 0) + 1);
  return [...counts]
    .map(([classType, count]) => ({ classType, count }))
    .sort((a, b) => (b.count === a.count ? a.classType.localeCompare(b.classType) : b.count - a.count));
}

/**
 * Whether inputs are links. ComfyUI links use `[上游节点 id, 输出序号]`; `{"__value__": [...]}`
 * wraps ecosystem array literals, explicitly author-declared and bindable.
 *
 * Inspect wrappers before shapes, matching server `workflow.is_link`; unwrapping first would misclassify
 * `{"__value__": ["4", 0]}` as links, hiding genuinely assignable inputs from manual selectors.
 */
export function isLinkInput(value: unknown): boolean {
  if (isWrappedValue(value)) return false;
  return Array.isArray(value) && value.length === 2 && typeof value[1] === "number";
}

function isWrappedValue(value: unknown): value is { __value__: unknown } {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    "__value__" in value
  );
}

function unwrapValue(value: unknown): unknown {
  return isWrappedValue(value) ? value.__value__ : value;
}

/** Current input literals for manual selectors; links/absent inputs yield no values. */
export function literalInputText(node: ComfyuiNodeEntry, input: string): string {
  const value = unwrapValue(node.inputs[input]);
  if (value === undefined) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}

/**
 * All manually selectable targets. Outputs are node-level (without `input`); other semantic keys require literal inputs,
 * because values written into links are overwritten upstream and server validators reject them.
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
 * Ordered-list selection/deselection: candidates follow candidate-list order (inference ties follow node order),
 * manually selected noncandidates append in insertion order; list positions define reference-image indices.
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

/** Targets match by node for outputs, node plus field otherwise. */
export function sameTarget(a: ComfyuiBindingTarget, b: ComfyuiBindingTarget): boolean {
  return a.node === b.node && (a.input ?? null) === (b.input ?? null);
}

/** Workflow cells written by targets; readonly `fps` writes no values and occupies no cells. */
function targetLanding(target: ComfyuiBindingTarget): string | null {
  if (target.direction === "read") return null;
  return target.input === undefined ? `#${target.node}` : `#${target.node}.${target.input}`;
}

// ---------------------------------------------------------------------------
// Inference results → pending bindings
// ---------------------------------------------------------------------------

/**
 * Persisted inference shape: only unique highest-score keys automatically include entries; ambiguous/unconfirmed keys include none,
 * requiring user decisions. Explicit unsupported retains empty lists; other keys remain absent, never inferred.
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

/** Changed media removes out-of-scope semantic keys and entries, which would never receive values anyway. */
export function pruneBindings(bindings: ComfyuiBindings, mediaType: ComfyuiMediaType): ComfyuiBindings {
  const allowed = new Set<string>(bindingKeysFor(mediaType));
  const next: ComfyuiBindings = {};
  for (const [key, targets] of Object.entries(bindings)) {
    if (allowed.has(key)) next[key as ComfyuiBindingKey] = targets;
  }
  return next;
}

/** Candidate-list indices of currently selected entries for this key. */
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
 * Row states: user edits this round or entries from saved definitions (reused/rematched) count as manually specified;
 * `docs/adr/0082` treats every saved binding as already user-confirmed.
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
    // Tied candidates precede required-unbound states: users should choose available candidates rather than search anew.
    if (state === "ambiguous" || state === "needs_confirmation") return "ambiguous";
    return isRequiredBindingKey(key) ? "required_unbound" : "not_found";
  }
  if (options.touched) return "manual";
  const origins = boundOrigins(options.candidates, targets);
  return origins.length > 0 && origins.every((origin) => origin === "inferred") ? "auto" : "manual";
}

/** Origins of row entries; manually selected noncandidate entries are absent. */
function boundOrigins(
  candidates: ComfyuiBindingCandidate[],
  targets: ComfyuiBindingTarget[],
): ComfyuiMatchOrigin[] {
  return candidates
    .filter((candidate) => targets.some((target) => sameTarget(target, candidate.target)))
    .map((candidate) => candidate.origin);
}

/**
 * Reimport marks: any type-plus-title-migrated entry means Rematched, requiring closer user review;
 * all unchanged entries mean Reused; freshly inferred/manual rows show no marks.
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

/** Four header-side count categories. */
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
// Save gates
// ---------------------------------------------------------------------------

/** Save-blocking reasons: `key` renders semantic names, `code` selects explanatory copy. */
export interface ComfyuiSaveBlocker {
  code: "required_unbound" | "needs_choice" | "needs_binding" | "target_taken" | "placeholder_name";
  key?: ComfyuiBindingKey;
  /** `target_taken` only: other semantic keys occupying identical cells. */
  otherKey?: ComfyuiBindingKey;
}

/**
 * Save-blocking reasons, empty when saveable. Three server hard gates require prompt/output targets,
 * user resolution of tied candidates, and distinct fields for semantic keys; additionally require renaming placeholder `meta`,
 * preventing unrelated same-author/name workflows from counting as duplicates.
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
    // Bindings lost on reimport with no reinferred candidates require manual selection or explicit unsupported marking.
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

/**
 * Whether definitions fail even endpoint tests.
 *
 * Only two save blockers are also server **validator** hard gates (`comfyui_binding_required` and
 * `comfyui_target_collision`), causing preview/test 422s. Three others are not: unresolved tied candidates
 * and invalidated old bindings merely leave dimensions unbound; previews still render, correctly explaining dimensions
 * not driving workflows. Placeholder names block saving only.
 */
export function testRefused(blockers: readonly ComfyuiSaveBlocker[]): boolean {
  return blockers.some((blocker) => blocker.code === "required_unbound" || blocker.code === "target_taken");
}

/**
 * Canonical definition shapes for changed-state comparison.
 *
 * Raw string comparison misclassifies identical contents: manually selected empty titles omit `title`
 * (see `withTitle`), whereas server rematches return `"title": ""`; key order also varies by origin.
 * Recursively sort keys and treat empty `title` as absent for common comparison. Only button copy/enabled states change;
 * persisted values remain original `draft`.
 */
export function definitionFingerprint(definition: object): string {
  return JSON.stringify(canonical(definition));
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value === null || typeof value !== "object") return value;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([key, raw]) => !(key === "title" && raw === ""))
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return Object.fromEntries(entries.map(([key, raw]) => [key, canonical(raw)]));
}
