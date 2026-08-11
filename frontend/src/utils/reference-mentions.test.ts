import { describe, it, expect } from "vitest";
import {
  buildMentionLookup,
  extractMentions,
  matchDialogueLine,
  matchVoiceoverLine,
  normalizeAssetName,
  resolveMentionType,
  mergeReferences,
  splitScriptLines,
} from "./reference-mentions";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

function mkProject(): Pick<ProjectData, "characters" | "scenes" | "props" | "products"> {
  return {
    characters: { 主角: { description: "" }, 张三: { description: "" }, "角色甲（成年）": { description: "" }, 角色乙: { description: "" } },
    scenes: { 酒馆: { description: "" }, "地点甲·版本A": { description: "" } },
    props: { 长剑: { description: "" }, 载具甲: { description: "" }, 道具甲: { description: "" } },
    products: { 水杯: { description: "", brand: "" } },
  };
}

describe("extractMentions", () => {
  it("returns unique mention names in first-occurrence order", () => {
    expect(extractMentions("@a @b @a @c")).toEqual(["a", "b", "c"]);
  });

  it("returns empty list when no mentions", () => {
    expect(extractMentions("镜头1：plain text")).toEqual([]);
  });

  it("matches CJK characters and underscores", () => {
    expect(extractMentions("@主角 and @张_三")).toEqual(["主角", "张_三"]);
  });

  it("matches wrapped names containing punctuation", () => {
    expect(extractMentions("@[角色甲（成年）] 接近 @[地点甲·版本A]")).toEqual([
      "角色甲（成年）",
      "地点甲·版本A",
    ]);
  });

  it("matches wrapped names adjacent to verbs", () => {
    expect(extractMentions("@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作")).toEqual([
      "角色甲（成年）",
      "角色乙",
      "载具甲",
      "道具甲",
    ]);
  });

  it("rejects non-ascii legacy mentions to stay aligned with backend", () => {
    expect(extractMentions("@éclair @한글 @张三 @abc_123")).toEqual(["张三", "abc_123"]);
  });

  it("rejects curly-brace wrapped mentions", () => {
    expect(extractMentions("@[角色甲（成年）] 与 @{道具甲}")).toEqual(["角色甲（成年）"]);
  });
});

describe("parser output is normalized", () => {
  // 解析器承诺输出规范形（NFC + 去 BOM），与后端 shot_parser._normalize_source 同口径：
  // 调用方拿到的名字可直接与已归一的资产表 key 判等，不再各自补归一。
  const nameNfc = "Hiếu".normalize("NFC");
  const nameNfd = "Hiếu".normalize("NFD");
  const BOM = "\uFEFF";

  it("has distinct NFC / NFD byte forms in the fixtures", () => {
    expect(nameNfc).not.toBe(nameNfd);
  });

  it("emits NFC mention names regardless of the encoding written in the text", () => {
    expect(extractMentions(`镜头1：@[${nameNfd}] 登场`)).toEqual([nameNfc]);
  });

  it("dedupes mentions across NFC / NFD spellings of the same asset", () => {
    expect(extractMentions(`@[${nameNfc}] 与 @[${nameNfd}]`)).toEqual([nameNfc]);
  });

  it("strips BOM from inside a mention name", () => {
    // `@[名<BOM>称]` 类粘贴产物：后端解析入口去 BOM，前端不去就会判未登记，预览与生成结果不一致
    expect(extractMentions(`镜头1：@[张${BOM}三] 抬眼`)).toEqual(["张三"]);
    expect(extractMentions(`${BOM}@[张三] 抬眼`)).toEqual(["张三"]);
  });

  it("reads a line as a normative dialogue line despite BOM and NFD", () => {
    expect(matchDialogueLine(`${BOM}@[张${BOM}三]：{我${BOM}来了}`)).toEqual({
      speaker: "张三",
      text: "我来了",
    });
    expect(matchDialogueLine(`@[${nameNfd}]：{我来了}`)).toEqual({ speaker: nameNfc, text: "我来了" });
  });

  it("reads a bare braces line as voiceover despite BOM", () => {
    expect(matchVoiceoverLine(`${BOM}{那年冬天}`)).toBe("那年冬天");
  });

  it("keeps a BOM-laced speaker slot out of mention extraction", () => {
    // BOM 让前端判规范行、后端判描述行时，说话人是否进参考图两侧结论相反
    expect(extractMentions(`@[张${BOM}三]：{我来了}`)).toEqual([]);
  });

  it("emits normalized script lines", () => {
    expect(splitScriptLines(`${BOM}镜头1：中景\n@[${nameNfd}]：{我来了}`)).toEqual([
      "镜头1：中景",
      `@[${nameNfc}]：{我来了}`,
    ]);
  });

  it("resolves a BOM-laced mention against the registered bucket", () => {
    const merged = mergeReferences(`镜头1：@[张${BOM}三] 抬眼`, [], mkProject());
    expect(merged).toEqual([{ type: "character", name: "张三" }]);
  });
});

describe("resolveMentionType", () => {
  const project = mkProject();

  it("resolves every registered asset type", () => {
    expect(resolveMentionType(project, "主角")).toBe("character");
    expect(resolveMentionType(project, "酒馆")).toBe("scene");
    expect(resolveMentionType(project, "长剑")).toBe("prop");
    expect(resolveMentionType(project, "水杯")).toBe("product");
  });

  it("returns undefined for unknown names", () => {
    expect(resolveMentionType(project, "路人")).toBeUndefined();
  });

  it("does not disambiguate a corrupt duplicate namespace at runtime", () => {
    const corrupt = { characters: { Shared: {} }, scenes: { Shared: {} }, props: {}, products: {} };
    expect(resolveMentionType(corrupt as never, "Shared")).toBeUndefined();
  });

  // toString / constructor / __proto__ 都通得过 validate_asset_name，用 `in` 查会命中
  // 原型链，把没登记的名字判成已登记
  it("does not resolve prototype-chain property names as registered assets", () => {
    for (const name of ["toString", "constructor", "hasOwnProperty", "__proto__"]) {
      expect(resolveMentionType(project, name)).toBeUndefined();
    }
  });

  it("resolves an asset actually named like a prototype property", () => {
    const withOddName = { characters: { toString: { description: "" } }, scenes: {}, props: {} };
    expect(resolveMentionType(withOddName as never, "toString")).toBe("character");
  });
});

describe("buildMentionLookup", () => {
  it("normalizes keys and removes corrupt cross-bucket duplicates", () => {
    const lookup = buildMentionLookup({
      characters: Object.fromEntries([[" café ", {}], ["__proto__", {}]]),
      scenes: { "cafe\u0301": {} },
      props: { toString: {} },
      products: { Product: {} },
    } as never);

    expect(Object.getPrototypeOf(lookup)).toBeNull();
    expect(Object.hasOwn(lookup, "café")).toBe(false);
    expect(lookup.__proto__).toBe("character");
    expect(lookup.toString).toBe("prop");
    expect(lookup.Product).toBe("product");
  });

  it("does not select a runtime owner when a product duplicates another bucket", () => {
    const lookup = buildMentionLookup({
      characters: { Shared: {} },
      scenes: {},
      props: {},
      products: { Shared: {} },
    } as never);

    expect(Object.hasOwn(lookup, "Shared")).toBe(false);
  });
});

describe("normalizeAssetName", () => {
  it("uses Python strip whitespace without removing U+FEFF", () => {
    expect(normalizeAssetName("\u001c\u3000Hero\u00a0")).toBe("Hero");
    expect(normalizeAssetName("\uFEFFHero")).toBe("\uFEFFHero");
  });

  it("does not conflate an asset starting with U+FEFF with the visible name", () => {
    const project = {
      characters: { "\uFEFFHero": { description: "" } },
      scenes: {},
      props: {},
    };
    expect(resolveMentionType(project as never, "Hero")).toBeUndefined();
    expect(resolveMentionType(project as never, "\uFEFFHero")).toBe("character");
  });
});

describe("mergeReferences", () => {
  const project = mkProject();

  it("appends new mentions at the end, preserving existing order", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
    ];
    const merged = mergeReferences("镜头1：@张三 @主角", existing, project);
    expect(merged).toEqual([
      { type: "character", name: "张三" },
      { type: "character", name: "主角" },
    ]);
  });

  it("removes references whose names are no longer in prompt", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
      { type: "scene", name: "酒馆" },
    ];
    const merged = mergeReferences("镜头1：@张三", existing, project);
    expect(merged).toEqual([{ type: "character", name: "张三" }]);
  });

  it("skips unknown mentions (not resolvable to any bucket)", () => {
    const merged = mergeReferences("镜头1：@路人 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("canonicalizes padded wrapped mentions before emitting references", () => {
    const merged = mergeReferences("镜头1：@[ 主角 ] 入场", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("does not emit products into the generic reference-video schema", () => {
    expect(mergeReferences("镜头1：@水杯 特写", [], project)).toEqual([]);
  });

  it("deduplicates repeated mentions", () => {
    const merged = mergeReferences("镜头1：@主角 @主角 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("merges wrapped references", () => {
    const merged = mergeReferences("Shot 1 (8s): @[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域", [], project);
    expect(merged).toEqual([
      { type: "character", name: "角色甲（成年）" },
      { type: "character", name: "角色乙" },
      { type: "prop", name: "载具甲" },
    ]);
  });

  it("returns empty list when prompt has no valid mentions", () => {
    expect(mergeReferences("镜头1：plain", [], project)).toEqual([]);
  });

  it("resolves a registered name across NFC/NFD encoding mismatch", () => {
    const nameNfc = "Hiếu".normalize("NFC");
    const nameNfd = "Hiếu".normalize("NFD");
    expect(nameNfc).not.toBe(nameNfd);
    const projectWithCombining = {
      characters: { [nameNfd]: { description: "" } },
      scenes: {},
      props: {},
    };
    const merged = mergeReferences(`镜头1：@[${nameNfc}] 登场`, [], projectWithCombining);
    expect(merged).toEqual([{ type: "character", name: nameNfc }]);
  });
});

describe("MENTION_RE prefix boundary", () => {
  it("ignores email-like prefix", () => {
    expect(extractMentions("contact a@张三")).toEqual([]);
    expect(extractMentions("test@domain.com")).toEqual([]);
    expect(extractMentions("alice@example.com 和 bob@foo.io")).toEqual([]);
    expect(extractMentions("room9@张三")).toEqual([]);
    expect(extractMentions("user123@李四")).toEqual([]);
  });

  it("accepts Chinese prefix", () => {
    expect(extractMentions("你好@张三")).toEqual(["张三"]);
    expect(extractMentions("（对面）@李四")).toEqual(["李四"]);
  });

  it("accepts whitespace / line-start / punctuation prefix", () => {
    expect(extractMentions("@张三")).toEqual(["张三"]);
    expect(extractMentions("之后 @张三")).toEqual(["张三"]);
    expect(extractMentions("Shot 1 (3s):\n@张三")).toEqual(["张三"]);
    expect(extractMentions("台词：@张三")).toEqual(["张三"]);
  });

  it("preserves valid mention next to email-shape prefix", () => {
    expect(extractMentions("contact a@张三 then @李四 shows up")).toEqual(["李四"]);
  });

  it("rejects underscore prefix", () => {
    expect(extractMentions("prefix_@张三")).toEqual([]);
  });

  it("mergeReferences drops email-shape references", () => {
    const project = {
      characters: { 张三: { character_sheet: "c/1.png" } },
      scenes: {},
      props: {},
    } as const;
    const refs = mergeReferences("contact a@张三", [], project as never);
    expect(refs).toEqual([]);
  });
});

describe("normative dialogue lines", () => {
  it("matches `@[角色]：{台词}` with either colon, wrapped or bare", () => {
    expect(matchDialogueLine("@[张三]：{我来了}")).toEqual({ speaker: "张三", text: "我来了" });
    expect(matchDialogueLine("@张三:{我来了}")).toEqual({ speaker: "张三", text: "我来了" });
    expect(matchDialogueLine("  @[角色甲（成年）] ： {我来了} ")).toEqual({
      speaker: "角色甲（成年）",
      text: "我来了",
    });
  });

  it("rejects dialogue mixed into a description line", () => {
    expect(matchDialogueLine("中景，@[张三]：{我来了} 说完转身")).toBeNull();
    expect(matchDialogueLine("他说 @[张三]：{我来了}")).toBeNull();
    expect(matchDialogueLine("@[张三]：{我来了")).toBeNull();
  });

  it("reads a bare braces line as voiceover", () => {
    expect(matchVoiceoverLine("  {那年冬天格外冷}  ")).toBe("那年冬天格外冷");
    expect(matchVoiceoverLine("旁白：{那年冬天}")).toBeNull();
  });

  it("keeps speaker slots out of mention extraction", () => {
    // 与后端 shot_parser.extract_mentions 同口径：给画外说话的角色附参考图会诱导它入画
    expect(extractMentions("镜头1：@酒馆 内景。\n@张三：{我来了}\n镜头2：@张三 抬眼。")).toEqual([
      "酒馆",
      "张三",
    ]);
    expect(extractMentions("@张三：{我来了}")).toEqual([]);
  });

  it("keeps a speaker slot written on the shot header line out of mentions", () => {
    // 后端切分镜头时剥掉 header，这行在 shot 文本里就是规范行 —— 两侧须同判
    expect(extractMentions("镜头1：@[张三]：{我来了}")).toEqual([]);
    expect(extractMentions("镜头1：@酒馆 内景。\n镜头2：@张三：{我来了}")).toEqual(["酒馆"]);
  });

  it("strips a shot header written with non-ASCII digits", () => {
    // Python `\d` 认全角/阿拉伯-印度数字，后端会剥掉 header 并把这行判成台词；
    // JS `\d` 只认 ASCII，若不对齐，前端会把说话人当成参考图留下来
    expect(extractMentions("镜头１：@[张三]：{我来了}")).toEqual([]);
    expect(extractMentions("镜头٣：@[张三]：{我来了}")).toEqual([]);
    expect(extractMentions("镜头１：@酒馆 内景。")).toEqual(["酒馆"]);
  });

  it("does not treat a blank speaker slot as a normative line", () => {
    // 同后端 match_dialogue_line：speaker 位全为空白不算规范行，否则会派生出非法 utterance
    expect(matchDialogueLine("@[ ]：{我来了}")).toBeNull();
    expect(extractMentions("@[ ]：{我来了}")).toEqual([" "]);
  });

  it("does not treat blank braces as an utterance", () => {
    // 同后端：utterance 的 text 必须非空，空台词不派生
    expect(matchVoiceoverLine("{}")).toBeNull();
    expect(matchVoiceoverLine("{   }")).toBeNull();
    expect(matchDialogueLine("@[张三]：{}")).toBeNull();
  });

  it("keeps speaker-only characters out of merged references", () => {
    const refs = mergeReferences("镜头1：@酒馆 内景。\n@张三：{我来了}", [], mkProject());
    expect(refs).toEqual([{ type: "scene", name: "酒馆" }]);
  });
});

describe("splitScriptLines", () => {
  // 与 Python str.splitlines() 逐例对齐：末尾换行不多出空行，行中间的空行照常保留
  it.each([
    ["", []],
    ["a", ["a"]],
    ["a\n", ["a"]],
    ["a\n\n", ["a", ""]],
    ["a\n\nb", ["a", "", "b"]],
    ["\n", [""]],
    ["镜头1：中景\r\n", ["镜头1：中景"]],
    ["镜头1：中景 ", ["镜头1：中景"]],
  ])("splits %j the way splitlines() does", (input, expected) => {
    expect(splitScriptLines(input)).toEqual(expected);
  });
});
