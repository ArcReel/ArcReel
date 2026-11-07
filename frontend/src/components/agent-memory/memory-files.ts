import type { AgentMemoryOverview, AgentMemoryScope, AgentMemoryType } from "@/types/agent-memory";
import { parseIsoTimestamp } from "@/utils/date-format";

/** Index files are absent from list-response `files`, counted separately in `index`; frontends pin them as virtual entries accordingly. */
export const INDEX_FILENAME = "MEMORY.md";
export const INDEX_LINE_LIMIT = 200;

/** Selected files live in URL `file=` parameters; `file=new` is the new-file form (valid filenames end in .md, preventing collisions). */
export const MEMORY_FILE_PARAM = "file";
export const NEW_MEMORY_FILE = "new";

/** Same criteria as `lib/agent/agent_memory_store.py`; local prevalidation only exposes duplicate/mistyped names locally, with servers authoritative. */
const FILENAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._() -]*\.md$/;
const FILENAME_MAX_LENGTH = 100;

export type MemoryEntry =
  | { kind: "index"; name: string; lineCount: number; overLimit: boolean }
  | {
      kind: "topic";
      name: string;
      description: string | null;
      type: AgentMemoryType | null;
      modifiedAt: string;
    };

/** List order: index first, memory files newest-modified first. */
export function memoryEntries(overview: AgentMemoryOverview | null): MemoryEntry[] {
  if (!overview) return [];
  const topics = [...overview.files]
    .sort((a, b) => parseIsoTimestamp(b.modified_at).getTime() - parseIsoTimestamp(a.modified_at).getTime())
    .map<MemoryEntry>((file) => ({
      kind: "topic",
      name: file.name,
      description: file.frontmatter?.description ?? null,
      type: file.frontmatter?.type ?? null,
      modifiedAt: file.modified_at,
    }));
  const index: MemoryEntry[] = overview.index.exists
    ? [
        {
          kind: "index",
          name: INDEX_FILENAME,
          lineCount: overview.index.line_count,
          overLimit: overview.index.over_limit,
        },
      ]
    : [];
  return [...index, ...topics];
}

export type MemorySelection = { kind: "file"; entry: MemoryEntry } | { kind: "new"; empty: boolean };

/**
 * Resolve URL parameters into selections: `file=new` selects new-file forms; nonexistent files (deleted, cleared or mistyped) fall back to first entries;
 * empty lists show new-file forms. Delete/clear leave URLs unchanged; selections settle during rendering.
 */
export function resolveSelection(param: string | null, entries: MemoryEntry[]): MemorySelection {
  if (param === NEW_MEMORY_FILE) return { kind: "new", empty: entries.length === 0 };
  const entry = entries.find((item) => item.name === param) ?? entries[0];
  return entry ? { kind: "file", entry } : { kind: "new", empty: true };
}

export type NewNameProblem = "invalid" | "duplicate";

/** Names are case-insensitively unique: on default case-insensitive filesystems, names differing only by case overwrite existing files. */
export function checkNewName(name: string, entries: MemoryEntry[]): NewNameProblem | null {
  if (name.length > FILENAME_MAX_LENGTH || !FILENAME_PATTERN.test(name)) return "invalid";
  const folded = name.toLowerCase();
  if (entries.some((entry) => entry.name.toLowerCase() === folded)) return "duplicate";
  return null;
}

/** New-file bodies: frontmatter templates with `type` user or project according to memory level. */
export function newFileTemplate(
  filename: string,
  level: AgentMemoryScope["level"],
  description: string,
  body: string,
): string {
  const name = filename.replace(/\.md$/, "");
  return `---\nname: ${name}\ndescription: ${description}\ntype: ${level}\n---\n\n${body}\n`;
}

/** Full server file paths, for display only. */
export function memoryFilePath(dir: string, filename: string): string {
  return dir ? `${dir.replace(/\/+$/, "")}/${filename}` : filename;
}
