// PROTOTYPE — wayfinder #2310 内存 store：两级记忆（关于你 / 本项目）各一组文件，MEMORY.md 索引 + 主题文件，
// 与 CLI auto memory 的落盘形态一致（frontmatter name/description/metadata.type）。不落盘、刷新即重置。
// 提供「模拟 Agent 写入」动作，用来在原型里制造空态、并发写入与保存冲突。评审后整目录删除。
import { useSyncExternalStore } from "react";

export type MemoryLevel = "user" | "project";
export type MemoryType = "user" | "feedback" | "project" | "reference";

export interface MemoryFile {
  name: string;
  content: string;
  modifiedAt: number;
  modifiedBy: "agent" | "you";
}

export interface LevelState {
  files: MemoryFile[];
  /** 有会话正在向该目录写入（原型里由「模拟」按钮置 3 秒）。 */
  agentWriting: boolean;
  lastAgentWriteAt: number | null;
}

export const INDEX_FILE = "MEMORY.md";
export const INDEX_LINE_LIMIT = 200;
export const INDEX_BYTE_LIMIT = 25_000;

export const TYPE_LABELS: Record<MemoryType, string> = {
  user: "用户",
  feedback: "反馈",
  project: "项目",
  reference: "参考",
};

export const TYPE_TONE: Record<MemoryType, string> = {
  user: "#9565c7",
  feedback: "#c48225",
  project: "#248fcc",
  reference: "#339c6d",
};

export const LEVEL_COPY: Record<
  MemoryLevel,
  { title: string; short: string; desc: string; path: string; empty: string }
> = {
  user: {
    title: "用户记忆",
    short: "用户记忆",
    desc: "跨项目生效的 Agent 记忆，记录你的偏好与习惯。会话开始时加载索引文件，主题文件按需读取。",
    path: "<数据根>/.arcreel/users/default/memory/",
    empty: "暂无记忆。Agent 会在创作过程中自动记录，也可以手动新建文件。",
  },
  project: {
    title: "项目记忆",
    short: "项目记忆",
    desc: "仅在本项目会话中生效的 Agent 记忆，记录项目设定与修改要求，随项目目录保存。",
    path: "<项目目录>/.arcreel/memory/",
    empty: "暂无记忆。Agent 会在创作过程中自动记录，也可以手动新建文件。",
  },
};

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function topic(name: string, description: string, type: MemoryType, body: string): string {
  return `---\nname: ${name}\ndescription: ${description}\nmetadata:\n  type: ${type}\n---\n${body.trim()}\n`;
}

function seedUser(now: number): MemoryFile[] {
  return [
    {
      name: INDEX_FILE,
      modifiedAt: now - 2 * DAY,
      modifiedBy: "agent",
      content: [
        "- [画幅偏好](aspect-ratio.md) — 竖屏 9:16 优先，横屏只在明确要求时用",
        "- [配音口味](voice-preference.md) — 女声、语速偏慢、少用感叹句",
        "- [不要自作主张改剧情](feedback-no-plot-changes.md) — 改稿只动分镜描述，情节以原文为准",
        "",
      ].join("\n"),
    },
    {
      name: "aspect-ratio.md",
      modifiedAt: now - 9 * DAY,
      modifiedBy: "agent",
      content: topic(
        "aspect-ratio",
        "创作者的默认画幅偏好",
        "user",
        "创作者做的都是短视频平台内容，默认竖屏 9:16。\n\n**Why:** 三个项目都主动把横屏改回了竖屏。\n**How to apply:** 新项目不要询问画幅，直接按 9:16 规划分镜；只有创作者明确说「横屏」才切换。",
      ),
    },
    {
      name: "voice-preference.md",
      modifiedAt: now - 5 * DAY,
      modifiedBy: "agent",
      content: topic(
        "voice-preference",
        "配音音色与语速偏好",
        "user",
        "偏好女声、语速偏慢、平稳叙述；不喜欢旁白里出现感叹句。\n\n**How to apply:** 旁白文本写完后检查一遍，感叹号改句号；试听候选音色时先给「温和女声」。",
      ),
    },
    {
      name: "feedback-no-plot-changes.md",
      modifiedAt: now - 2 * DAY,
      modifiedBy: "agent",
      content: topic(
        "feedback-no-plot-changes",
        "改稿时不要改动原文情节",
        "feedback",
        "创作者要求分镜脚本严格跟随原著情节，不要为了「更适合视频」擅自增删事件。\n\n**Why:** 第二次改稿时 Agent 合并了两场戏，创作者明确表示不接受。\n**How to apply:** 想调整情节先问；默认只改镜头描述、旁白措辞和节奏。",
      ),
    },
  ];
}

function seedProject(now: number): MemoryFile[] {
  return [
    {
      name: INDEX_FILE,
      modifiedAt: now - 3 * HOUR,
      modifiedBy: "agent",
      content: [
        "- [主角视觉锚点](protagonist-look.md) — 林昭：束发、靛蓝短打、左眉有疤，所有资产图沿用",
        "- [风格禁忌](style-no-cyberpunk.md) — 不要霓虹 / 赛博朋克色调，整体偏水墨青灰",
        "- [第三集旁白太长](feedback-ep3-narration.md) — 每镜旁白控制在两句以内",
        "- [分镜命名规则](reference-shot-naming.md) — ep03_s02_shot05 形式，序号两位",
        "",
      ].join("\n"),
    },
    {
      name: "protagonist-look.md",
      modifiedAt: now - 6 * DAY,
      modifiedBy: "agent",
      content: topic(
        "protagonist-look",
        "主角林昭的固定视觉特征",
        "project",
        "林昭：二十出头，束发，靛蓝色短打，左眉尾一道浅疤，腰间挂一枚青铜铃。\n\n**Why:** 第一集资产图定稿后创作者要求后续所有集数保持一致。\n**How to apply:** 生成任何含林昭的画面提示词时，把以上四点原样写进提示词。",
      ),
    },
    {
      name: "style-no-cyberpunk.md",
      modifiedAt: now - 4 * DAY,
      modifiedBy: "agent",
      content: topic(
        "style-no-cyberpunk",
        "整体色调禁忌",
        "feedback",
        "创作者否决了带霓虹、高饱和青紫的画面，整体定为水墨青灰、低饱和。\n\n**How to apply:** 提示词里不要出现 neon / cyberpunk / 霓虹；配色词用「青灰」「宣纸」「淡墨」。",
      ),
    },
    {
      name: "feedback-ep3-narration.md",
      modifiedAt: now - 3 * HOUR,
      modifiedBy: "agent",
      content: topic(
        "feedback-ep3-narration",
        "第三集旁白过长的反馈",
        "feedback",
        "创作者认为第三集旁白挤占了画面时间。\n\n**How to apply:** 每个分镜旁白不超过两句；能用画面交代的信息不写进旁白。",
      ),
    },
    {
      name: "reference-shot-naming.md",
      modifiedAt: now - 8 * DAY,
      modifiedBy: "agent",
      content: topic(
        "reference-shot-naming",
        "分镜与素材命名规则",
        "reference",
        "分镜 id 形如 `ep03_s02_shot05`：集数、场次、镜号各两位。素材文件沿用分镜 id 作前缀。",
      ),
    },
  ];
}

type Store = Record<MemoryLevel, LevelState>;

let state: Store = {
  user: { files: seedUser(Date.now()), agentWriting: false, lastAgentWriteAt: Date.now() - 2 * DAY },
  project: { files: seedProject(Date.now()), agentWriting: false, lastAgentWriteAt: Date.now() - 3 * HOUR },
};

const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function patch(level: MemoryLevel, fn: (s: LevelState) => LevelState) {
  state = { ...state, [level]: fn(state[level]) };
  emit();
}

export function useMemoryLevel(level: MemoryLevel): LevelState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state[level],
  );
}

/** 事件处理器里同步读当前值（hook 之外）。 */
export function peekLevel(level: MemoryLevel): LevelState {
  return state[level];
}

export type SaveResult = "ok" | "conflict";

export const memoryActions = {
  /** 保存；若文件在 `loadedAt` 之后被别人改过则报冲突（不写入）。`force` 跳过检查。 */
  save(level: MemoryLevel, name: string, content: string, loadedAt: number, force = false): SaveResult {
    const cur = state[level].files.find((f) => f.name === name);
    if (cur && cur.modifiedAt !== loadedAt && !force) return "conflict";
    patch(level, (s) => ({
      ...s,
      files: cur
        ? s.files.map((f) => (f.name === name ? { ...f, content, modifiedAt: Date.now(), modifiedBy: "you" } : f))
        : [...s.files, { name, content, modifiedAt: Date.now(), modifiedBy: "you" }],
    }));
    return "ok";
  },
  create(level: MemoryLevel, name: string, content: string) {
    patch(level, (s) => ({
      ...s,
      files: [...s.files.filter((f) => f.name !== name), { name, content, modifiedAt: Date.now(), modifiedBy: "you" }],
    }));
  },
  remove(level: MemoryLevel, name: string) {
    patch(level, (s) => ({ ...s, files: s.files.filter((f) => f.name !== name) }));
  },
  clear(level: MemoryLevel) {
    patch(level, (s) => ({ ...s, files: [] }));
  },
  reseed(level: MemoryLevel) {
    const now = Date.now();
    patch(level, () => ({
      files: level === "user" ? seedUser(now) : seedProject(now),
      agentWriting: false,
      lastAgentWriteAt: now - (level === "user" ? 2 * DAY : 3 * HOUR),
    }));
  },
  /** 模拟 Agent 一次完整写入：3 秒「正在写入」，随后新增一个主题文件并在索引尾部追加一行。 */
  simulateAgentNewMemory(level: MemoryLevel) {
    patch(level, (s) => ({ ...s, agentWriting: true }));
    window.setTimeout(() => {
      const n = state[level].files.filter((f) => f.name.startsWith("agent-note-")).length + 1;
      const name = `agent-note-${n}.md`;
      const title = level === "user" ? `字幕字号偏好 ${n}` : `第 ${n + 3} 集片尾定格反馈`;
      const body = topic(
        name.replace(/\.md$/, ""),
        title,
        level === "user" ? "user" : "feedback",
        level === "user"
          ? "创作者两次把字幕字号从默认改大到 1.3 倍。\n\n**How to apply:** 合成字幕时默认字号 ×1.3。"
          : "创作者希望片尾定格在主角背影而不是黑场。\n\n**How to apply:** 每集最后一镜以人物背影收束，再淡出。",
      );
      const now = Date.now();
      patch(level, (s) => {
        const idx = s.files.find((f) => f.name === INDEX_FILE);
        const line = `- [${title}](${name}) — Agent 刚刚记下的一条\n`;
        const files = s.files.filter((f) => f.name !== INDEX_FILE);
        const index: MemoryFile = idx
          ? { ...idx, content: idx.content.replace(/\n*$/, "\n") + line, modifiedAt: now, modifiedBy: "agent" }
          : { name: INDEX_FILE, content: line, modifiedAt: now, modifiedBy: "agent" };
        return {
          agentWriting: false,
          lastAgentWriteAt: now,
          files: [index, ...files, { name, content: body, modifiedAt: now, modifiedBy: "agent" }],
        };
      });
    }, 3000);
  },
  /** 模拟 Agent 改写指定文件（在末尾追加一行），用来制造与你正在编辑的内容的冲突。 */
  simulateAgentTouch(level: MemoryLevel, name: string) {
    patch(level, (s) => ({ ...s, agentWriting: true }));
    window.setTimeout(() => {
      const now = Date.now();
      patch(level, (s) => ({
        agentWriting: false,
        lastAgentWriteAt: now,
        files: s.files.map((f) =>
          f.name === name
            ? {
                ...f,
                modifiedAt: now,
                modifiedBy: "agent",
                content:
                  f.content.replace(/\n*$/, "\n") +
                  (name === INDEX_FILE ? "- [Agent 补记](agent-touch.md) — 会话中追加的一行\n" : "\n**补充：** Agent 在本次会话里又确认了一次这条约定。\n"),
              }
            : f,
        ),
      }));
    }, 1500);
  },
};

// ---------------------------------------------------------------------------
// 派生工具
// ---------------------------------------------------------------------------

export interface ParsedTopic {
  name: string;
  description: string;
  type: MemoryType;
  body: string;
  hasFrontmatter: boolean;
}

const TYPES = new Set<string>(["user", "feedback", "project", "reference"]);

export function parseTopic(file: MemoryFile): ParsedTopic {
  const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(file.content);
  if (!m) {
    return { name: file.name.replace(/\.md$/, ""), description: "", type: "reference", body: file.content, hasFrontmatter: false };
  }
  const fm = m[1];
  const get = (k: string) => new RegExp(`^\\s*${k}:\\s*(.+)$`, "m").exec(fm)?.[1]?.trim() ?? "";
  const t = get("type");
  return {
    name: get("name") || file.name.replace(/\.md$/, ""),
    description: get("description"),
    type: TYPES.has(t) ? (t as MemoryType) : "reference",
    body: m[2].trim(),
    hasFrontmatter: true,
  };
}

export function buildTopic(name: string, description: string, type: MemoryType, body: string): string {
  return topic(name, description, type, body);
}

export interface IndexEntry {
  title: string;
  file: string;
  hook: string;
  raw: string;
}

export function parseIndex(content: string): IndexEntry[] {
  const out: IndexEntry[] = [];
  for (const raw of content.split("\n")) {
    const m = /^\s*-\s*\[([^\]]+)\]\(([^)]+)\)\s*(?:[—–-]+\s*(.*))?$/.exec(raw);
    if (m) out.push({ title: m[1], file: m[2], hook: (m[3] ?? "").trim(), raw });
  }
  return out;
}

export function indexStats(content: string): { lines: number; bytes: number; over: boolean } {
  const lines = content.replace(/\n$/, "").split("\n").filter((l) => l.trim() !== "").length;
  const bytes = new TextEncoder().encode(content).length;
  return { lines, bytes, over: lines > INDEX_LINE_LIMIT || bytes > INDEX_BYTE_LIMIT };
}

export function relTime(ts: number): string {
  const d = Date.now() - ts;
  if (d < 45_000) return "刚刚";
  if (d < HOUR) return `${Math.round(d / MIN)} 分钟前`;
  if (d < DAY) return `${Math.round(d / HOUR)} 小时前`;
  return `${Math.round(d / DAY)} 天前`;
}

export function slugify(title: string): string {
  const s = title
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return (s || "memory") + "-" + Math.random().toString(36).slice(2, 6);
}
