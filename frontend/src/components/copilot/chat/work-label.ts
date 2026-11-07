import type { TFunction } from "i18next";
import {
  Bot,
  Eye,
  FilePen,
  FilePlus,
  FileText,
  Film,
  FolderSearch,
  Globe,
  ListChecks,
  ListTree,
  MessageCircleQuestion,
  Search,
  Sparkles,
  SquareTerminal,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { ContentBlock, EpisodeMeta, TodoItem } from "@/types";
import { episodeDisplayName, episodeItemLabel, type ProjectScripts } from "@/utils/episode-display";
import { ARCREEL_TOOL_SUMMARIES } from "./arcreel-tool-summaries";

// ---------------------------------------------------------------------------
// Work-row display layers: qualifying blocks, states, and tool display names/icons/one-sentence summaries.
// Translate recognized arguments into creator-readable wording; only unknown arguments fall back to truncated raw text.
// ---------------------------------------------------------------------------

export type ToolInput = Record<string, unknown>;

/** Success has no marks; Running/Stopped depend on whether sessions still run or ended. */
export type WorkStatus = "running" | "ok" | "error" | "stopped";

/** Work blocks: consecutive work forms compact columns within reply rounds, with paragraph gaps only between bodies. */
export function isWorkBlock(block: ContentBlock): boolean {
  return (
    block.type === "tool_use" ||
    block.type === "tool_result" ||
    block.type === "thinking" ||
    block.type === "skill_invocation" ||
    block.type === "task_progress"
  );
}

export interface BlockSegment {
  work: boolean;
  /** Blocks within sections and their positions in original contents. */
  items: Array<{ block: ContentBlock; index: number }>;
}

/** Segment by work: consecutive work forms sections; other blocks each form separate sections. */
export function segmentWorkBlocks(blocks: ContentBlock[]): BlockSegment[] {
  const segments: BlockSegment[] = [];
  blocks.forEach((block, index) => {
    const work = isWorkBlock(block);
    const last = segments.at(-1);
    if (work && last?.work) last.items.push({ block, index });
    else segments.push({ work, items: [{ block, index }] });
  });
  return segments;
}

/** tool_use states: results mean finished; ended sessions without results mean Stopped, preventing dangling Running states. */
export function toolStatus(block: ContentBlock, sessionDone: boolean): WorkStatus {
  if (block.is_error) return "error";
  if (block.result !== undefined) return "ok";
  return sessionDone ? "stopped" : "running";
}

export type EpisodeRefs = readonly Pick<EpisodeMeta, "episode" | "title" | "script_file">[];

const SUMMARY_JOINER = " · ";
const MAX_LISTED = 3;
const MAX_FALLBACK_CHARS = 120;

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(str).filter(Boolean) : [];
}

function fileName(path: string): string {
  return path.split("/").pop() ?? path;
}

/** Summary-fragment formatters derived from current languages/project episode ledgers. */
export interface SummaryFormat {
  text: (value: unknown) => string;
  /** Paths retain final two segments only. */
  path: (value: unknown) => string;
  /** Episode IDs → episode titles, falling back to broadcast positions. */
  episode: (value: unknown) => string;
  /** Script filenames → owning episode titles; outside ledgers, show filenames. */
  script: (value: unknown) => string;
  /** Entry IDs → Episode title · S01. */
  item: (value: unknown) => string;
  /** Entry-ID groups: same episodes show Episode title · S01/S02; above three, list first three only. */
  items: (value: unknown) => string;
  /** Name groups list only first three when longer. */
  list: (value: unknown) => string;
  assetType: (value: unknown) => string;
  quote: (value: unknown) => string;
  arrow: (from: unknown, to: unknown) => string;
  revision: (value: unknown) => string;
  version: (value: unknown) => string;
  count: (key: "tool_summary_grids" | "tool_summary_operations", value: unknown) => string;
  projectFields: (input: ToolInput) => string;
  record: (value: unknown) => Record<string, unknown>;
  /** Specific fields of each array object. */
  field: (value: unknown, key: string) => unknown[];
  join: (...parts: string[]) => string;
}

export function createSummaryFormat(t: TFunction, episodes: EpisodeRefs, scripts: ProjectScripts = {}): SummaryFormat {
  const list = (value: unknown): string => {
    const names = strings(value);
    if (names.length === 0) return "";
    const listed = names.slice(0, MAX_LISTED).join(t("dashboard:tool_summary_separator"));
    if (names.length <= MAX_LISTED) return listed;
    return t("dashboard:tool_summary_more", { list: listed, total: names.length, rest: names.length - MAX_LISTED });
  };
  const episode = (value: unknown): string =>
    typeof value === "number" && Number.isInteger(value) ? episodeDisplayName(episodes, value, t) : "";
  const record = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

  return {
    text: str,
    path: (value) => str(value).split("/").slice(-2).join("/"),
    episode,
    script: (value) => {
      const name = fileName(str(value));
      if (!name) return "";
      const hit = episodes.find((entry) => fileName(entry.script_file) === name);
      return hit ? episode(hit.episode) : name;
    },
    item: (value) => {
      const id = str(value);
      return id ? episodeItemLabel(id, scripts, episodes, t) : "";
    },
    items: (value) => {
      const ids = strings(value);
      if (ids.length === 0) return "";
      const labels = ids.map((id) => episodeItemLabel(id, scripts, episodes, t));
      // Same-episode entries show titles once: Episode 1 · 1, 2.
      const prefixes = new Set(labels.map((label) => label.split(SUMMARY_JOINER)[0]));
      if (prefixes.size === 1 && labels.every((label) => label.includes(SUMMARY_JOINER))) {
        const [prefix] = prefixes;
        return `${prefix}${SUMMARY_JOINER}${list(labels.map((label) => label.slice(prefix.length + SUMMARY_JOINER.length)))}`;
      }
      return list(labels);
    },
    list,
    assetType: (value) => {
      const type = str(value);
      return type ? t(`dashboard:task_type_${type}`, { defaultValue: type }) : "";
    },
    quote: (value) => {
      const text = str(value);
      return text ? t("dashboard:tool_summary_quoted", { text }) : "";
    },
    arrow: (from, to) => (str(from) && str(to) ? `${str(from)} → ${str(to)}` : ""),
    revision: (value) => (typeof value === "number" ? t("dashboard:tool_summary_revision", { revision: value }) : ""),
    version: (value) => (typeof value === "number" ? t("dashboard:tool_summary_version", { version: value }) : ""),
    count: (key, value) => (Array.isArray(value) && value.length > 0 ? t(`dashboard:${key}`, { count: value.length }) : ""),
    projectFields: (input) => {
      if (input.overview != null) return t("dashboard:tool_summary_project_overview");
      if (input.settings != null) return t("dashboard:tool_summary_project_settings");
      return "";
    },
    record,
    field: (value, key) => (Array.isArray(value) ? value.map((entry) => record(entry)[key]) : []),
    join: (...parts) => parts.filter(Boolean).join(SUMMARY_JOINER),
  };
}

const ARCREEL_TOOL_PREFIX = /^mcp__arcreel__([a-z0-9_]+)$/;

const BUILTIN_TOOLS: Record<string, { key: string; icon: LucideIcon }> = {
  Read: { key: "tool_builtin_read", icon: FileText },
  Write: { key: "tool_builtin_write", icon: FilePlus },
  Edit: { key: "tool_builtin_edit", icon: FilePen },
  Bash: { key: "tool_builtin_bash", icon: SquareTerminal },
  BashOutput: { key: "tool_builtin_bash_output", icon: SquareTerminal },
  KillBash: { key: "tool_builtin_kill_bash", icon: SquareTerminal },
  Grep: { key: "tool_builtin_grep", icon: Search },
  Glob: { key: "tool_builtin_glob", icon: FolderSearch },
  WebSearch: { key: "tool_builtin_web_search", icon: Globe },
  WebFetch: { key: "tool_builtin_web_fetch", icon: Globe },
  TodoWrite: { key: "tool_builtin_todo_write", icon: ListChecks },
  AskUserQuestion: { key: "tool_builtin_ask_user_question", icon: MessageCircleQuestion },
};

function arcreelToolIcon(id: string): LucideIcon {
  if (id.startsWith("generate") || id.startsWith("edit_images")) return Sparkles;
  if (id.startsWith("inspect") || id.startsWith("get") || id.startsWith("list") || id.startsWith("read")) return Eye;
  if (id.includes("timeline") || id.includes("revision") || id.includes("final_cut") || id.includes("jianying")) return Film;
  if (id.includes("draft") || id.startsWith("patch") || id.startsWith("edit")) return FilePen;
  return Wrench;
}

function builtinSummary(name: string, input: ToolInput, t: TFunction, f: SummaryFormat): string | null {
  switch (name) {
    case "Read":
    case "Write":
    case "Edit":
      return f.path(input.file_path);
    case "Bash":
      return f.text(input.description) || f.text(input.command);
    case "Grep":
      return f.quote(input.pattern);
    case "Glob":
      return f.text(input.pattern);
    case "WebSearch":
      return f.text(input.query);
    case "WebFetch":
      return f.text(input.url);
    case "TodoWrite": {
      const todos = Array.isArray(input.todos) ? (input.todos as TodoItem[]) : [];
      const completed = todos.filter((todo) => todo.status === "completed").length;
      return todos.length > 0 ? t("dashboard:tool_summary_todo", { completed, total: todos.length }) : "";
    }
    case "AskUserQuestion":
      return f.join(...f.field(input.questions, "question").map(str));
    case "BashOutput":
    case "KillBash":
      return "";
    default:
      return null;
  }
}

function fallbackSummary(input: ToolInput): string {
  if (Object.keys(input).length === 0) return "";
  const json = JSON.stringify(input);
  return json.length > MAX_FALLBACK_CHARS ? `${json.slice(0, MAX_FALLBACK_CHARS)}…` : json;
}

export interface WorkLabel {
  name: string;
  summary: string;
  icon: LucideIcon;
}

/** Tool display names/icons/summaries. ArcReel/built-in tools use localized names; others retain original names. */
export function toolLabel(
  name: string,
  input: ToolInput | undefined,
  t: TFunction,
  episodes: EpisodeRefs,
  scripts: ProjectScripts = {},
): WorkLabel {
  const args = input ?? {};
  const f = createSummaryFormat(t, episodes, scripts);

  const arcreel = ARCREEL_TOOL_PREFIX.exec(name);
  if (arcreel) {
    const id = arcreel[1];
    const summarize = ARCREEL_TOOL_SUMMARIES[id];
    return {
      name: t(`dashboard:tool_name_${id}`, { defaultValue: id }),
      summary: summarize ? summarize(args, f) : fallbackSummary(args),
      icon: arcreelToolIcon(id),
    };
  }

  const builtin = BUILTIN_TOOLS[name];
  return {
    name: builtin ? t(`dashboard:${builtin.key}`) : name,
    summary: builtinSummary(name, args, t, f) ?? fallbackSummary(args),
    icon: builtin?.icon ?? Wrench,
  };
}

export const SUBAGENT_ICON = Bot;
export const SKILL_ICON = Zap;
export const BACKGROUND_TASK_ICON = ListTree;
export const TOOL_RESULT_ICON = Wrench;
