import { describe, it, expect } from "vitest";
import {
  buildMentionLookup,
  extractMentions,
  lineSpeechMarks,
  normalizeAssetName,
  splitScriptLines,
  splitSpeechLine,
  stripSpeechMarks,
} from "./reference-mentions";
import type { ProjectData } from "@/types";

/** 记号断言的短写法：`[speaker, text]`，speaker 为空串即画外音。 */
function marks(line: string): [string, string][] {
  return lineSpeechMarks(line).map((mark) => [mark.speaker, mark.text]);
}

/** 判定路径的输入来自已归一的 `splitScriptLines`；直测原语时补同一道归一。 */
function normalize(text: string): string {
  return splitScriptLines(text)[0] ?? "";
}
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
    expect(extractMentions("plain text")).toEqual([]);
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
  // 解析器承诺输出规范形（NFC + 去 BOM），与后端 text_parser._normalize_source 同口径：
  // 调用方拿到的名字可直接与已归一的资产表 key 判等，不再各自补归一。
  const nameNfc = "Hiếu".normalize("NFC");
  const nameNfd = "Hiếu".normalize("NFD");
  const BOM = "\uFEFF";

  it("has distinct NFC / NFD byte forms in the fixtures", () => {
    expect(nameNfc).not.toBe(nameNfd);
  });

  it("emits NFC mention names regardless of the encoding written in the text", () => {
    expect(extractMentions(`@[${nameNfd}] 登场`)).toEqual([nameNfc]);
  });

  it("dedupes mentions across NFC / NFD spellings of the same asset", () => {
    expect(extractMentions(`@[${nameNfc}] 与 @[${nameNfd}]`)).toEqual([nameNfc]);
  });

  it("strips wrapped names before lookup and deduplication", () => {
    expect(extractMentions("@[ Hero ] 与 @[Hero] @Hero")).toEqual(["Hero"]);
  });

  it("strips BOM from inside a mention name", () => {
    // `@[名<BOM>称]` 类粘贴产物：后端解析入口去 BOM，前端不去就会判未登记，预览与生成结果不一致
    expect(extractMentions(`@[张${BOM}三] 抬眼`)).toEqual(["张三"]);
    expect(extractMentions(`${BOM}@[张三] 抬眼`)).toEqual(["张三"]);
  });

  it("reads a dialogue mark despite BOM and NFD", () => {
    expect(marks(normalize(`${BOM}@[张${BOM}三]：{我${BOM}来了}`))).toEqual([["张三", "我来了"]]);
    expect(marks(normalize(`@[${nameNfd}]：{我来了}`))).toEqual([[nameNfc, "我来了"]]);
  });

  it("reads bare braces as voiceover despite BOM", () => {
    expect(marks(normalize(`${BOM}{那年冬天}`))).toEqual([["", "那年冬天"]]);
  });

  it("keeps a BOM-laced speaker slot out of mention extraction", () => {
    // BOM 让前端判规范行、后端判描述行时，说话人是否进参考图两侧结论相反
    expect(extractMentions(`@[张${BOM}三]：{我来了}`)).toEqual([]);
  });

  it("emits normalized script lines", () => {
    expect(splitScriptLines(`${BOM}中景\n@[${nameNfd}]：{我来了}`)).toEqual([
      "中景",
      `@[${nameNfc}]：{我来了}`,
    ]);
  });

  it("resolves a BOM-laced mention against the registered bucket", () => {
    const lookup = buildMentionLookup(mkProject());
    expect(extractMentions(`@[张${BOM}三] 抬眼`).map((name) => lookup[name])).toEqual(["character"]);
  });
});

describe("buildMentionLookup", () => {
  it("normalizes keys and resolves cross-bucket duplicates by stable priority", () => {
    const lookup = buildMentionLookup({
      characters: Object.fromEntries([[" café ", {}], ["__proto__", {}]]),
      scenes: { "cafe\u0301": {} },
      props: { toString: {} },
      products: { Product: {} },
    } as never);

    expect(Object.getPrototypeOf(lookup)).toBeNull();
    expect(lookup["café"]).toBe("character");
    expect(lookup.__proto__).toBe("character");
    expect(lookup.toString).toBe("prop");
    expect(lookup.Product).toBe("product");
  });

  it("gives products priority when a name is present in another bucket", () => {
    const lookup = buildMentionLookup({
      characters: { Shared: {} },
      scenes: {},
      props: {},
      products: { Shared: {} },
    } as never);

    expect(lookup.Shared).toBe("product");
  });
});

describe("normalizeAssetName", () => {
  it("uses Python strip whitespace without removing U+FEFF", () => {
    expect(normalizeAssetName("\u001c\u3000Hero\u00a0")).toBe("Hero");
    expect(normalizeAssetName("\uFEFFHero")).toBe("\uFEFFHero");
  });

  it("does not conflate an asset starting with U+FEFF with the visible name", () => {
    const lookup = buildMentionLookup({
      characters: { "\uFEFFHero": { description: "" } },
      scenes: {},
      props: {},
    } as never);
    expect(Object.hasOwn(lookup, "Hero")).toBe(false);
    expect(lookup["\uFEFFHero"]).toBe("character");
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

});

describe("inline speech marks", () => {
  it("matches `@[角色]{台词}` with either colon, wrapped or bare, and no separator", () => {
    expect(marks("@[张三]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@张三:{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("  @[角色甲（成年）] ： {我来了} ")).toEqual([["角色甲（成年）", "我来了"]]);
    expect(marks("@[ 张三 ]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三]{我来了}")).toEqual([["张三", "我来了"]]);
  });

  it("reads speech written inline after a description", () => {
    expect(marks("中景，@[张三] 笑着，@[张三]{我来了} 说完转身")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三]{你来了}@[李四]{我来了}")).toEqual([
      ["张三", "你来了"],
      ["李四", "我来了"],
    ]);
  });

  it("does not guess a speaker from a mention that is not adjacent to the braces", () => {
    expect(marks("他说 @[张三] 转身，屋里传出 {我来了}")).toEqual([["", "我来了"]]);
    expect(marks("@[张三]：{我来了")).toEqual([]);
  });

  it("reads bare braces as voiceover anywhere in the line", () => {
    expect(marks("  {那年冬天格外冷}  ")).toEqual([["", "那年冬天格外冷"]]);
    expect(marks("旁白：{那年冬天}")).toEqual([["", "那年冬天"]]);
  });

  it("splits a line losslessly", () => {
    const line = "@[张三] 推门。@[张三]{我来了}屋里安静。";
    const joined = splitSpeechLine(line)
      .map((part) => (typeof part === "string" ? part : part.raw))
      .join("");
    expect(joined).toBe(line);
  });

  it("keeps speaker slots out of mention extraction", () => {
    // 与后端 text_parser.extract_mentions 同口径：给画外说话的角色附参考图会诱导它入画
    expect(extractMentions("@酒馆 内景。\n@张三：{我来了}\n@张三 抬眼。")).toEqual([
      "酒馆",
      "张三",
    ]);
    expect(extractMentions("@张三：{我来了}")).toEqual([]);
  });

  it("keeps speaker slots out of mentions across lines", () => {
    expect(extractMentions("@酒馆 内景。\n@张三：{我来了}")).toEqual(["酒馆"]);
  });

  it("does not treat a blank speaker slot as a speech mark", () => {
    // 同后端 split_speech_line：speaker 位全为空白不成记号，否则会派生出非法 utterance
    expect(marks("@[ ]：{我来了}")).toEqual([]);
    expect(extractMentions("@[ ]：{我来了}")).toEqual([""]);
  });

  it("does not fall back to voiceover when the speaker slot is malformed", () => {
    // 作者写的是「某人说」，静默改判画外音比不识别更难发现
    expect(marks("@[]：{我来了}")).toEqual([]);
  });

  it("does not fall back to voiceover when the separator colon is repeated", () => {
    // 同后端 split_speech_line：只吞一个分隔冒号，剩下的冒号说明这不是台词形态
    expect(marks("@[张三]：：{我来了}")).toEqual([]);
    expect(stripSpeechMarks("@[张三]：：{我来了}")).toBe("@[张三]：：{我来了}");
    expect(marks("门开了。@[张三]:: {我来了}")).toEqual([]);
  });

  it("still binds the speaker across a single separator colon", () => {
    expect(marks("@[张三]：{我来了}")).toEqual([["张三", "我来了"]]);
    expect(marks("@[张三] : {我来了}")).toEqual([["张三", "我来了"]]);
  });

  it("counts the unit separator as inline whitespace like Python", () => {
    // JS 的 `\s` 不含 U+001F 而 Python 的 str.isspace() 含：少这一个字符，
    // 说话人会在后端绑定、在前端派生成参考图
    expect(marks("@[张三]\u001f{我来了}")).toEqual([["张三", "我来了"]]);
    expect(extractMentions("@[张三]\u001f{我来了}")).toEqual([]);
  });

  it("does not read nested braces as one mark", () => {
    // 同后端 split_speech_line：外层 `{` 不成记号，从内层重新扫描
    expect(marks("{外层 {内层}}")).toEqual([["", "内层"]]);
  });

  it("does not treat blank braces as an utterance", () => {
    // 同后端：utterance 的 text 必须非空，空台词不派生
    expect(marks("{}")).toEqual([]);
    expect(marks("{   }")).toEqual([]);
    expect(marks("@[张三]：{}")).toEqual([]);
  });

  it("keeps mentions written outside the marks in the reference derivation", () => {
    expect(extractMentions("@[酒馆] 内景。@[张三]{我来了}")).toEqual(["酒馆"]);
    expect(extractMentions("@[张三] 推门。@[张三]{我来了}")).toEqual(["张三"]);
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
    ["中景\r\n", ["中景"]],
    ["中景 ", ["中景"]],
  ])("splits %j the way splitlines() does", (input, expected) => {
    expect(splitScriptLines(input)).toEqual(expected);
  });
});
