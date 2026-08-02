import { describe, it, expect } from "vitest";
import {
  extractMentions,
  matchDialogueLine,
  matchVoiceoverLine,
  resolveMentionType,
  mergeReferences,
} from "./reference-mentions";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

function mkProject(): Pick<ProjectData, "characters" | "scenes" | "props"> {
  return {
    characters: { 主角: { description: "" }, 张三: { description: "" }, "角色甲（成年）": { description: "" }, 角色乙: { description: "" } },
    scenes: { 酒馆: { description: "" }, "地点甲·版本A": { description: "" } },
    props: { 长剑: { description: "" }, 载具甲: { description: "" }, 道具甲: { description: "" } },
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

describe("resolveMentionType", () => {
  const project = mkProject();

  it("prefers character → scene → prop", () => {
    expect(resolveMentionType(project, "主角")).toBe("character");
    expect(resolveMentionType(project, "酒馆")).toBe("scene");
    expect(resolveMentionType(project, "长剑")).toBe("prop");
  });

  it("returns undefined for unknown names", () => {
    expect(resolveMentionType(project, "路人")).toBeUndefined();
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

  it("does not treat a blank speaker slot as a normative line", () => {
    // 同后端 match_dialogue_line：speaker 位全为空白不算规范行，否则会派生出非法 utterance
    expect(matchDialogueLine("@[ ]：{我来了}")).toBeNull();
    expect(extractMentions("@[ ]：{我来了}")).toEqual([" "]);
  });

  it("keeps speaker-only characters out of merged references", () => {
    const refs = mergeReferences("镜头1：@酒馆 内景。\n@张三：{我来了}", [], mkProject());
    expect(refs).toEqual([{ type: "scene", name: "酒馆" }]);
  });
});
