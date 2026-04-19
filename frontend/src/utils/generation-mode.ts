/**
 * Generation mode helpers — mirrors lib/project_manager.py:effective_mode().
 *
 * Canonical values: "storyboard" | "grid" | "reference_video".
 * Legacy value "single" (old projects) is normalized to "storyboard".
 */

export type GenerationMode = "storyboard" | "grid" | "reference_video";

const CANONICAL: readonly GenerationMode[] = ["storyboard", "grid", "reference_video"];

export function normalizeMode(value: unknown): GenerationMode {
  if (value === "single") return "storyboard";
  if (typeof value === "string" && (CANONICAL as readonly string[]).includes(value)) {
    return value as GenerationMode;
  }
  return "storyboard";
}

export function effectiveMode(
  project: { generation_mode?: string | null } | null | undefined,
  episode: { generation_mode?: string | null } | null | undefined,
): GenerationMode {
  const ep = episode?.generation_mode;
  if (typeof ep === "string") {
    const normalized = normalizeMode(ep);
    // only respect episode override if it's a valid mode string
    if (ep === "single" || (CANONICAL as readonly string[]).includes(ep)) return normalized;
  }
  const proj = project?.generation_mode;
  if (typeof proj === "string") {
    const normalized = normalizeMode(proj);
    if (proj === "single" || (CANONICAL as readonly string[]).includes(proj)) return normalized;
  }
  return "storyboard";
}
