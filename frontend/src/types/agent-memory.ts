// Read model for the Agent memory directory (the overview response from backend lib/agent/agent_memory_store.py).
// User and project memory have identical shapes and differ only in request paths, so they share these types.

/** Valid memory types in frontmatter; the backend treats any other value as absent frontmatter. */
export type AgentMemoryType = "user" | "feedback" | "project" | "reference";

/** Successfully parsed frontmatter; `name` / `description` is null when absent or entirely whitespace. */
export interface AgentMemoryFrontmatter {
  name: string | null;
  description: string | null;
  type: AgentMemoryType;
}

export interface AgentMemoryFile {
  name: string;
  size: number;
  /** UTC ISO 8601 with a timezone offset. */
  modified_at: string;
  /** Null in its entirety when frontmatter is missing, malformed, or has an invalid type. */
  frontmatter: AgentMemoryFrontmatter | null;
}

/** Statistics for the MEMORY.md index file, which does not appear in `files`. */
export interface AgentMemoryIndexStats {
  exists: boolean;
  line_count: number;
  byte_size: number;
  over_limit: boolean;
}

export interface AgentMemoryOverview {
  /** Absolute server path of the memory directory, shown directly to the creator without i18n. */
  path: string;
  index: AgentMemoryIndexStats;
  files: AgentMemoryFile[];
}

/** Memory ownership scope: the two levels differ only in request paths and wording. */
export type AgentMemoryScope = { level: "user" } | { level: "project"; projectName: string };
