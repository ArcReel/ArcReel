/**
 * Generation mode helpers — mirrors lib/project_manager.py:effective_mode().
 *
 * Canonical values: "storyboard" | "grid" | "reference_video".
 * Legacy value "single" (old projects) is normalized to "storyboard".
 */

export type GenerationMode = "storyboard" | "grid" | "reference_video";

const CANONICAL: readonly GenerationMode[] = ["storyboard", "grid", "reference_video"];

/** All recognized input strings (canonical + legacy alias). */
const RECOGNIZED = new Set<string>(["single", ...CANONICAL]);

function isCanonical(v: string): v is GenerationMode {
  return (CANONICAL as readonly string[]).includes(v);
}

export function normalizeMode(value: unknown): GenerationMode {
  if (value === "single") return "storyboard";
  if (typeof value === "string" && isCanonical(value)) return value;
  return "storyboard";
}

export function effectiveMode(
  project: { generation_mode?: string | null } | null | undefined,
  episode: { generation_mode?: string | null } | null | undefined,
): GenerationMode {
  const ep = episode?.generation_mode;
  if (typeof ep === "string" && RECOGNIZED.has(ep)) return normalizeMode(ep);
  const proj = project?.generation_mode;
  if (typeof proj === "string" && RECOGNIZED.has(proj)) return normalizeMode(proj);
  return "storyboard";
}
