// PROTOTYPE（#2752）：剪辑时间线只读预览的样例数据与派生逻辑。一次性代码，不进 main。
// 结构按 ADR 0090 / #2673 的决策：剪辑片段引用视频单元、跟随 current，记录裁切所依据的版本；
// 转场挂在相邻片段之间、以切点为中心、不改变总时长；旁白挂在视频单元的第一个剪辑片段上，可延伸。

const ASSET = "/prototype-edit-timeline";

export interface SourceUnit {
  id: string;
  title: string;
  src: string;
  thumb: string;
  duration: number;
  currentVersion: number;
  deleted?: boolean;
  narration?: { src: string; duration: number; text: string };
}

export type TransitionKind = "叠化" | "闪黑" | "闪白" | "推移" | "擦除" | "圆形遮罩";

export interface Clip {
  id: string;
  unitId: string;
  in: number;
  out: number;
  trimmedVersion: number;
  volume: number;
  reason?: string;
}

export interface EditTimeline {
  id: string;
  name: string;
  revision: number;
  updatedAt: string;
  updatedBy: string;
  lastSummary: string;
  clips: Clip[];
  /** key = 左侧剪辑片段 ID */
  transitions: Record<string, { kind: TransitionKind; duration: number }>;
  bgm: { id: string; name: string; src: string; start: number; end: number; volume: number } | null;
}

export const UNITS: SourceUnit[] = [
  {
    id: "U01",
    title: "码头夜景，林舟独自站在路灯下",
    src: `${ASSET}/U01.mp4`,
    thumb: `${ASSET}/U01.jpg`,
    duration: 5,
    currentVersion: 1,
    narration: {
      src: `${ASSET}/narration-1.m4a`,
      duration: 5.54,
      text: "深夜的码头只剩下一盏灯，林舟握紧了那封没有署名的信。",
    },
  },
  { id: "U02", title: "信封特写，手指微微发抖", src: `${ASSET}/U02.mp4`, thumb: `${ASSET}/U02.jpg`, duration: 6, currentVersion: 3 },
  {
    id: "U03",
    title: "林舟走向仓库铁门，推门",
    src: `${ASSET}/U03.mp4`,
    thumb: `${ASSET}/U03.jpg`,
    duration: 8,
    currentVersion: 1,
    narration: {
      src: `${ASSET}/narration-2.m4a`,
      duration: 5.02,
      text: "她知道，只要推开这扇门，一切就再也回不去了。",
    },
  },
  { id: "U04", title: "铁门缓缓打开，光线涌出", src: `${ASSET}/U04.mp4`, thumb: `${ASSET}/U04.jpg`, duration: 5, currentVersion: 2 },
  { id: "U05", title: "（已从脚本删除）", src: `${ASSET}/U05.mp4`, thumb: `${ASSET}/U05.jpg`, duration: 5, currentVersion: 1, deleted: true },
  {
    id: "U06",
    title: "逆光中的人影转过身",
    src: `${ASSET}/U06.mp4`,
    thumb: `${ASSET}/U06.jpg`,
    duration: 6,
    currentVersion: 2,
    narration: {
      src: `${ASSET}/narration-3.m4a`,
      duration: 3.92,
      text: "门后站着的，是十年前就该死去的哥哥。",
    },
  },
  { id: "U07", title: "林舟后退半步", src: `${ASSET}/U07.mp4`, thumb: `${ASSET}/U07.jpg`, duration: 4, currentVersion: 1 },
  { id: "U08", title: "哥哥的脸部特写", src: `${ASSET}/U08.mp4`, thumb: `${ASSET}/U08.jpg`, duration: 5, currentVersion: 1 },
];

export const UNIT_BY_ID = Object.fromEntries(UNITS.map((u) => [u.id, u]));

export const TIMELINES: EditTimeline[] = [
  {
    id: "tl_agent",
    name: "Agent 初剪",
    revision: 7,
    updatedAt: "今天 14:32",
    updatedBy: "ArcReel Agent",
    lastSummary: "删除 U07：人物后退动作与下一镜衔接不上；U03 拆成两段，后半段做反打",
    clips: [
      { id: "c1", unitId: "U01", in: 0.4, out: 4.6, trimmedVersion: 1, volume: 0.6, reason: "去掉开头 0.4s 起幅停顿" },
      { id: "c2", unitId: "U02", in: 1.0, out: 5.2, trimmedVersion: 3, volume: 0.6, reason: "v3 手部稳定，v1、v2 手指崩坏；只保留抖动最明显的一段" },
      { id: "c3", unitId: "U03", in: 0, out: 3.2, trimmedVersion: 1, volume: 0.8, reason: "走向铁门，切在伸手之前" },
      { id: "c4", unitId: "U04", in: 1.2, out: 4.0, trimmedVersion: 1, volume: 0.8, reason: "只取开门的中段" },
      { id: "c5", unitId: "U05", in: 0, out: 5, trimmedVersion: 1, volume: 0.8 },
      { id: "c6", unitId: "U03", in: 4.5, out: 8.0, trimmedVersion: 1, volume: 0.8, reason: "复用 U03 后半段：推门动作作为反打" },
      { id: "c7", unitId: "U06", in: 0.5, out: 6.0, trimmedVersion: 2, volume: 0.7, reason: "转身动作完整保留" },
      { id: "c8", unitId: "U08", in: 0, out: 4.2, trimmedVersion: 1, volume: 0.7, reason: "4.2s 后面部出现崩坏帧" },
    ],
    transitions: {
      c1: { kind: "叠化", duration: 0.6 },
      c3: { kind: "闪黑", duration: 0.4 },
      c6: { kind: "推移", duration: 0.5 },
      c7: { kind: "闪白", duration: 0.3 },
    },
    bgm: { id: "b1", name: "暗潮（上传）", src: `${ASSET}/bgm.m4a`, start: 0, end: 30, volume: 0.35 },
  },
  {
    id: "tl_mech",
    name: "按脚本顺序",
    revision: 1,
    updatedAt: "昨天 21:05",
    updatedBy: "机械生成",
    lastSummary: "按当前脚本顺序生成，整段使用、全部硬切",
    clips: ["U01", "U02", "U03", "U04", "U05", "U06", "U07", "U08"].map((unitId, i) => ({
      id: `c${i + 1}`,
      unitId,
      in: 0,
      out: UNIT_BY_ID[unitId].duration,
      trimmedVersion: UNIT_BY_ID[unitId].currentVersion,
      volume: 1,
    })),
    transitions: {},
    bgm: null,
  },
];

export interface ResolvedClip {
  clip: Clip;
  unit: SourceUnit;
  index: number;
  /** 实际生效的入出点：裁切所依据的版本不是 current 时整段使用 */
  effIn: number;
  effOut: number;
  duration: number;
  start: number;
  end: number;
  staleTrim: boolean;
  missing: boolean;
  transitionOut?: { kind: TransitionKind; duration: number };
}

export interface NarrationPlacement {
  unitId: string;
  clipId: string;
  src: string;
  text: string;
  start: number;
  end: number;
}

export interface SubtitleCue {
  start: number;
  end: number;
  text: string;
}

export type IssueKind = "stale_trim" | "missing_unit" | "unused_unit" | "narration_overlap" | "narration_overflow";

export interface TimelineIssue {
  kind: IssueKind;
  severity: "warn" | "block";
  clipId?: string;
  unitId: string;
  message: string;
}

export interface ResolvedTimeline {
  timeline: EditTimeline;
  clips: ResolvedClip[];
  playable: ResolvedClip[];
  narrations: NarrationPlacement[];
  subtitles: SubtitleCue[];
  unusedUnits: SourceUnit[];
  issues: TimelineIssue[];
  total: number;
}

function splitCues(text: string, start: number, end: number): SubtitleCue[] {
  const parts = text.split(/(?<=[，。！？])/).filter(Boolean);
  const totalChars = parts.reduce((n, p) => n + p.length, 0);
  let t = start;
  return parts.map((p) => {
    const d = ((end - start) * p.length) / totalChars;
    const cue = { start: t, end: t + d, text: p.replace(/[，。]$/, "") };
    t += d;
    return cue;
  });
}

export function resolveTimeline(timeline: EditTimeline): ResolvedTimeline {
  let cursor = 0;
  const clips: ResolvedClip[] = timeline.clips.map((clip, index) => {
    const unit = UNIT_BY_ID[clip.unitId];
    const missing = Boolean(unit.deleted);
    const staleTrim = !missing && clip.trimmedVersion !== unit.currentVersion;
    const effIn = staleTrim ? 0 : clip.in;
    const effOut = staleTrim ? unit.duration : clip.out;
    const duration = missing ? 0 : effOut - effIn;
    const r: ResolvedClip = {
      clip,
      unit,
      index,
      effIn,
      effOut,
      duration,
      start: cursor,
      end: cursor + duration,
      staleTrim,
      missing,
      transitionOut: timeline.transitions[clip.id],
    };
    cursor += duration;
    return r;
  });
  const total = cursor;
  const playable = clips.filter((c) => !c.missing);

  const narrations: NarrationPlacement[] = [];
  const seen = new Set<string>();
  for (const c of playable) {
    if (seen.has(c.unit.id) || !c.unit.narration) continue;
    seen.add(c.unit.id);
    narrations.push({
      unitId: c.unit.id,
      clipId: c.clip.id,
      src: c.unit.narration.src,
      text: c.unit.narration.text,
      start: c.start,
      end: c.start + c.unit.narration.duration,
    });
  }
  const subtitles = narrations.flatMap((n) => splitCues(n.text, n.start, n.end));

  const issues: TimelineIssue[] = [];
  for (const c of clips) {
    if (c.missing)
      issues.push({
        kind: "missing_unit",
        severity: "warn",
        clipId: c.clip.id,
        unitId: c.unit.id,
        message: `${c.clip.id} 引用的视频单元 ${c.unit.id} 已从脚本删除，播放和渲染时跳过`,
      });
    if (c.staleTrim)
      issues.push({
        kind: "stale_trim",
        severity: "warn",
        clipId: c.clip.id,
        unitId: c.unit.id,
        message: `${c.clip.id} 的裁切按 v${c.clip.trimmedVersion} 设定，${c.unit.id} 现在用 v${c.unit.currentVersion}，已改为整段使用`,
      });
  }
  narrations.forEach((n, i) => {
    const next = narrations[i + 1];
    if (next && n.end > next.start)
      issues.push({
        kind: "narration_overlap",
        severity: "block",
        clipId: n.clipId,
        unitId: n.unitId,
        message: `${n.unitId} 的旁白与下一段旁白重叠 ${(n.end - next.start).toFixed(1)}s`,
      });
    if (n.end > total)
      issues.push({
        kind: "narration_overflow",
        severity: "block",
        clipId: n.clipId,
        unitId: n.unitId,
        message: `${n.unitId} 的旁白超出时间线末尾 ${(n.end - total).toFixed(1)}s`,
      });
  });
  const used = new Set(timeline.clips.map((c) => c.unitId));
  const unusedUnits = UNITS.filter((u) => !u.deleted && !used.has(u.id));
  for (const u of unusedUnits)
    issues.push({ kind: "unused_unit", severity: "warn", unitId: u.id, message: `视频单元 ${u.id} 没有用在这条剪辑时间线里` });

  return { timeline, clips, playable, narrations, subtitles, unusedUnits, issues, total };
}

export function fmt(t: number): string {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

export function clipAt(r: ResolvedTimeline, t: number): ResolvedClip | undefined {
  return r.playable.find((c) => t >= c.start && t < c.end) ?? r.playable[r.playable.length - 1];
}

export function unitHue(unitId: string): number {
  return (Number(unitId.replace(/\D/g, "")) * 67 + 210) % 360;
}
