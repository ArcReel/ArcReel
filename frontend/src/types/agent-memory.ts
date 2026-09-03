// Agent 记忆目录的读模型（后端 lib/agent_memory_store.py 的 overview 响应）。
// 用户记忆与项目记忆的形状完全一致，差别只在请求路径，故共用一套类型。

/** frontmatter 里合法的记忆类型；取值之外的一律由后端判为无 frontmatter。 */
export type AgentMemoryType = "user" | "feedback" | "project" | "reference";

/** 解析成功的 frontmatter；`name` / `description` 缺失或全空白时为 null。 */
export interface AgentMemoryFrontmatter {
  name: string | null;
  description: string | null;
  type: AgentMemoryType;
}

export interface AgentMemoryFile {
  name: string;
  size: number;
  /** 带时区偏移的 UTC ISO 8601。 */
  modified_at: string;
  /** frontmatter 缺失、语法错误或 type 非法时整体为 null。 */
  frontmatter: AgentMemoryFrontmatter | null;
}

/** 索引文件 MEMORY.md 的统计；它不出现在 `files` 里。 */
export interface AgentMemoryIndexStats {
  exists: boolean;
  line_count: number;
  byte_size: number;
  over_limit: boolean;
}

export interface AgentMemoryOverview {
  /** 记忆目录的服务端绝对路径，直接展示给创作者，不进 i18n。 */
  path: string;
  index: AgentMemoryIndexStats;
  files: AgentMemoryFile[];
}

/** 记忆的归属层级：两级只换请求路径与文案。 */
export type AgentMemoryScope = { level: "user" } | { level: "project"; projectName: string };
