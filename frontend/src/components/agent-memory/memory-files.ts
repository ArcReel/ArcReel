import type { AgentMemoryOverview, AgentMemoryScope, AgentMemoryType } from "@/types/agent-memory";
import { parseIsoTimestamp } from "@/utils/date-format";

export const INDEX_FILENAME = "MEMORY.md";
export const INDEX_LINE_LIMIT = 200;

export const MEMORY_FILE_PARAM = "file";
export const NEW_MEMORY_FILE = "new";

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

export function resolveSelection(param: string | null, entries: MemoryEntry[]): MemorySelection {
  if (param === NEW_MEMORY_FILE) return { kind: "new", empty: entries.length === 0 };
  const entry = entries.find((item) => item.name === param) ?? entries[0];
  return entry ? { kind: "file", entry } : { kind: "new", empty: true };
}

export type NewNameProblem = "invalid" | "duplicate";

export function checkNewName(name: string, entries: MemoryEntry[]): NewNameProblem | null {
  if (name.length > FILENAME_MAX_LENGTH || !FILENAME_PATTERN.test(name)) return "invalid";
  const folded = name.toLowerCase();
  if (entries.some((entry) => entry.name.toLowerCase() === folded)) return "duplicate";
  return null;
}

export function newFileTemplate(
  filename: string,
  level: AgentMemoryScope["level"],
  description: string,
  body: string,
): string {
  const name = filename.replace(/\.md$/, "");
  return `---\nname: ${name}\ndescription: ${description}\ntype: ${level}\n---\n\n${body}\n`;
}

export function memoryFilePath(dir: string, filename: string): string {
  return dir ? `${dir.replace(/\/+$/, "")}/${filename}` : filename;
}
