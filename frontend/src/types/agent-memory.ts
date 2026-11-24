
export type AgentMemoryType = "user" | "feedback" | "project" | "reference";

export interface AgentMemoryFrontmatter {
  name: string | null;
  description: string | null;
  type: AgentMemoryType;
}

export interface AgentMemoryFile {
  name: string;
  size: number;
  modified_at: string;
  frontmatter: AgentMemoryFrontmatter | null;
}

export interface AgentMemoryIndexStats {
  exists: boolean;
  line_count: number;
  byte_size: number;
  over_limit: boolean;
}

export interface AgentMemoryOverview {
  path: string;
  index: AgentMemoryIndexStats;
  files: AgentMemoryFile[];
}

export type AgentMemoryScope = { level: "user" } | { level: "project"; projectName: string };
