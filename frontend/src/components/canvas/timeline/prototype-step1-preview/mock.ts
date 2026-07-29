/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * 参考路径 step1 新格式（#1473：`{units: [{duration, source_text, text}]}`）
 * 按集预览面板的 mock 数据与玩具派生 parser。不接后端、不进生产。
 * 违约/警示文案取 #1449 定稿七条与 #1473 违约七类，硬编码中文（原型豁免 i18n）。
 */
import { MENTION_RE, mentionNameFromMatch } from "@/utils/reference-mentions";

// ---------- 类型（原型自足，不依赖生产 types） ----------

export interface ProtoUnit {
  unitId: string;
  /** unit 单时长（#1473：枚举，schema 卡死；档位值为占位示意） */
  duration: number;
  /** 原文锚：逐字子串 */
  sourceText: string;
  /** 书写层扁平文本（镜头行 / 规范台词行 / 描述行 / 裸画外音行） */
  text: string;
}

export type ProtoAssetKind = "character" | "scene" | "prop";

export interface ProtoAsset {
  kind: ProtoAssetKind;
  /** 是否设有参考音频（角色）——决定 A 类直通还是 B 类回落 */
  hasReferenceAudio?: boolean;
}

/** 阻断违约（#1473 七类中的示例子集），静态标注在 mock 上 */
export interface ProtoViolation {
  unitId: string;
  /** 违约类短名 */
  kind: string;
  /** 逐条定位 + 说明 */
  detail: string;
  /** 按处置路径撰写的修复指引 */
  fix: string;
  /** 锚定到书写层文本的行号（0-based，可选） */
  line?: number;
}

/** 非阻断 warning（#1449 定稿文案） */
export interface ProtoWarning {
  unitId: string;
  text: string;
  line?: number;
}

export interface ProtoScenario {
  /** 草稿隔离态（有阻断违约）还是正常待确认态 */
  quarantined: boolean;
  units: ProtoUnit[];
  violations: ProtoViolation[];
  warnings: ProtoWarning[];
}

// ---------- 登记资产表 ----------

export const PROTO_ASSETS: Record<string, ProtoAsset> = {
  沈青梧: { kind: "character", hasReferenceAudio: true },
  陆沉舟: { kind: "character", hasReferenceAudio: false },
  雨夜码头: { kind: "scene" },
  旧书店: { kind: "scene" },
  黄铜怀表: { kind: "prop" },
};

/** 时长枚举档位（占位示意，实际档位来自模型能力声明） */
export const DURATION_CHOICES = [5, 10, 15];

// ---------- mock 剧本（《雾锁长街》第 3 集） ----------

const UNIT_1: ProtoUnit = {
  unitId: "U1",
  duration: 10,
  sourceText:
    "码头的灯在雨里晕开一圈昏黄。沈青梧撑着伞站在灯下，听见身后传来熟悉的脚步声，十年了，那个人还是没变。陆沉舟从集装箱的阴影里走出来，风衣下摆往下滴水，掌心里攥着那块黄铜怀表。",
  text: `镜头1：雨夜的 @[雨夜码头]，@[沈青梧] 撑伞立于灯下，远处货轮鸣笛，雨丝在灯晕里斜落，镜头缓推。
@[沈青梧]：{十年了，你还是没变。}
镜头2：@[陆沉舟] 从集装箱阴影中走出，风衣下摆滴水，特写他掌心的 @[黄铜怀表]，表盖弹开。
@[陆沉舟]：{变的是这座城，不是我。}`,
};

const UNIT_2: ProtoUnit = {
  unitId: "U2",
  duration: 5,
  sourceText:
    "旧书店里灰尘浮动。她翻开那本泛黄的书，扉页上是他留下的字。窗外雨声渐密。",
  text: `镜头1：@[旧书店] 内光柱斜照，@[沈青梧] 翻开泛黄的书页说"这是他留下的"，灰尘在光柱中浮动。
@[陆沉舟]：{别碰那本书。}`,
};

const UNIT_3_VIOLATING: ProtoUnit = {
  unitId: "U3",
  duration: 5,
  sourceText: "顾十三推门而入，风铃轻响，他摘下帽子露出那道疤。",
  text: `镜头1：@[顾十三] 推门而入，风铃轻响，逆光中看不清面容。
@[顾十三]：{青梧，好久不见。这十年我走遍了南洋十三港，就是为了把当年码头上没说完的那句话，当面说给你听。
镜头2：@[沈青梧] 手中的书落地，尘埃惊起。`,
};

const UNIT_4: ProtoUnit = {
  unitId: "U4",
  duration: 10,
  sourceText:
    "长街的雨下了整夜。没有人知道十年前的那个雨夜发生了什么，真相和血迹一起，被雨水冲刷干净了。",
  text: `镜头1：俯拍雨中的长街，霓虹倒影碎在积水里，行人撑伞匆匆，节奏缓慢。
{当年的真相，被雨水冲刷了十年。}
镜头2：@[黄铜怀表] 落在积水中的特写，秒针停在十二点，涟漪一圈圈散开。`,
};

// ---------- warnings（#1449 定稿文案） ----------

const SHARED_WARNINGS: ProtoWarning[] = [
  { unitId: "U1", text: "角色「陆沉舟」未设置参考音频：台词声音将由模型自行决定", line: 3 },
  { unitId: "U2", text: "镜头1：台词与描述写在同一行，未识别为台词；如需声音参考请将台词单独成行（@[角色]：{台词}）", line: 0 },
  { unitId: "U2", text: "角色「陆沉舟」未设置参考音频：台词声音将由模型自行决定", line: 1 },
];

// ---------- 两种数据态 ----------

export const SCENARIO_CLEAN: ProtoScenario = {
  quarantined: false,
  units: [UNIT_1, UNIT_2, UNIT_4],
  violations: [],
  warnings: SHARED_WARNINGS,
};

export const SCENARIO_QUARANTINED: ProtoScenario = {
  quarantined: true,
  units: [UNIT_1, UNIT_2, UNIT_3_VIOLATING, UNIT_4],
  violations: [
    {
      unitId: "U3",
      kind: "未登记资产",
      detail: "@[顾十三] 未在角色/场景/道具中登记（镜头1 描述行与台词行 speaker 位）",
      fix: "先在角色库创建「顾十三」并生成参考图，或改用已登记的角色名",
      line: 0,
    },
    {
      unitId: "U3",
      kind: "花括号未闭合",
      detail: "台词行 {青梧，好久不见。…} 花括号未闭合，无法识别为台词",
      fix: "补齐右花括号 }，确认台词边界",
      line: 1,
    },
    {
      unitId: "U3",
      kind: "原文锚失配",
      detail: "source_text「顾十三推门而入，风铃轻响…」不是本集源文的逐字子串",
      fix: "从源文复制对应段落作为原文锚，不改写、不概括",
      line: undefined,
    },
    {
      unitId: "U3",
      kind: "台词超载",
      detail: "台词约 58 字，按语速下界需 ≥ 12s，超出 unit 时长 5s",
      fix: "缩短台词，或将 unit 时长上调至更大档位",
      line: 1,
    },
  ],
  warnings: SHARED_WARNINGS,
};

// ---------- 玩具派生 parser（只做派生预览，不做校验） ----------

export type ProtoLineKind = "shot" | "dialogue" | "voiceover" | "prose";

export interface ProtoLine {
  kind: ProtoLineKind;
  raw: string;
  /** dialogue 专用：speaker 名 */
  speaker?: string;
  /** dialogue / voiceover 专用：台词正文 */
  quote?: string;
  /** shot 专用：镜头序号 */
  shotNo?: number;
}

export interface ProtoDerived {
  lines: ProtoLine[];
  shotCount: number;
  utterances: { speaker: string | null; text: string }[];
  /** mention 首现顺序，排除规范台词行 speaker 位、排除未登记 */
  references: { name: string; kind: ProtoAssetKind }[];
  /** 未登记 mention（描述行内），编辑器侧 warning 素材 */
  unknownMentions: string[];
}

const SHOT_RE = /^镜头(\d+)\s*[：:]\s*(.*)$/;
const DIALOGUE_RE = /^@\[([^\]]+)\]\s*[：:]\s*\{([\s\S]*)\}\s*$/;
const VOICEOVER_RE = /^\{([\s\S]*)\}\s*$/;

export function deriveUnit(text: string): ProtoDerived {
  const lines: ProtoLine[] = [];
  const utterances: ProtoDerived["utterances"] = [];
  const references: ProtoDerived["references"] = [];
  const seenRefs = new Set<string>();
  const unknownMentions: string[] = [];
  const seenUnknown = new Set<string>();
  let shotCount = 0;

  const collectMentions = (fragment: string) => {
    for (const m of fragment.matchAll(MENTION_RE)) {
      const name = mentionNameFromMatch(m);
      const asset = PROTO_ASSETS[name];
      if (asset) {
        if (!seenRefs.has(name)) {
          seenRefs.add(name);
          references.push({ name, kind: asset.kind });
        }
      } else if (!seenUnknown.has(name)) {
        seenUnknown.add(name);
        unknownMentions.push(name);
      }
    }
  };

  for (const raw of text.split("\n")) {
    const shotMatch = SHOT_RE.exec(raw);
    if (shotMatch) {
      shotCount += 1;
      lines.push({ kind: "shot", raw, shotNo: Number(shotMatch[1]) });
      collectMentions(shotMatch[2]);
      continue;
    }
    const dialogueMatch = DIALOGUE_RE.exec(raw);
    if (dialogueMatch) {
      // 规范台词行：speaker 位不计入参考图派生（画外角色附图会诱导入画）
      lines.push({ kind: "dialogue", raw, speaker: dialogueMatch[1], quote: dialogueMatch[2] });
      utterances.push({ speaker: dialogueMatch[1], text: dialogueMatch[2] });
      continue;
    }
    const voMatch = VOICEOVER_RE.exec(raw.trim());
    if (voMatch && raw.trim()) {
      lines.push({ kind: "voiceover", raw, quote: voMatch[1] });
      utterances.push({ speaker: null, text: voMatch[1] });
      continue;
    }
    lines.push({ kind: "prose", raw });
    collectMentions(raw);
  }

  return { lines, shotCount, utterances, references, unknownMentions };
}
