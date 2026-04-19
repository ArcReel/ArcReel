import type { ProjectData } from "@/types";
import type { AssetKind, ReferenceResource } from "@/types/reference-video";

const MENTION_RE = /@([\w\u4e00-\u9fff]+)/g;

export function extractMentions(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  MENTION_RE.lastIndex = 0;
  for (;;) {
    const m = MENTION_RE.exec(text);
    if (!m) break;
    const name = m[1];
    if (!seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

type ProjectBuckets = Pick<ProjectData, "characters" | "scenes" | "props">;

export function resolveMentionType(
  project: ProjectBuckets | null | undefined,
  name: string,
): AssetKind | undefined {
  if (!project) return undefined;
  if (project.characters && name in project.characters) return "character";
  if (project.scenes && name in project.scenes) return "scene";
  if (project.props && name in project.props) return "prop";
  return undefined;
}

/**
 * Re-derive the references list for a unit given new prompt text.
 *
 * Rules:
 *  1. Preserve the order of `existing` entries whose names still appear in prompt.
 *  2. Drop entries whose names no longer appear.
 *  3. Append new mentions (in first-appearance order) that resolve to a known bucket.
 *  4. Skip unknown mentions (they become UI warning chips, not references).
 *  5. Deduplicate by name.
 */
export function mergeReferences(
  prompt: string,
  existing: ReferenceResource[],
  project: ProjectBuckets | null | undefined,
): ReferenceResource[] {
  const mentioned = new Set(extractMentions(prompt));
  const kept: ReferenceResource[] = [];
  const keptNames = new Set<string>();
  for (const ref of existing) {
    if (mentioned.has(ref.name) && !keptNames.has(ref.name)) {
      kept.push(ref);
      keptNames.add(ref.name);
    }
  }
  for (const name of mentioned) {
    if (keptNames.has(name)) continue;
    const type = resolveMentionType(project, name);
    if (!type) continue;
    kept.push({ type, name });
    keptNames.add(name);
  }
  return kept;
}
