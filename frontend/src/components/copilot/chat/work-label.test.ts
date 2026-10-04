import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import type { ContentBlock } from "@/types";
import { segmentWorkBlocks, toolLabel, type EpisodeRefs } from "./work-label";

const t = i18n.t.bind(i18n);

const EPISODES: EpisodeRefs = [
  { episode: 7, title: "", script_file: "scripts/episode_7.json" },
  { episode: 3, title: "雨夜", script_file: "scripts/episode_3.json" },
];

const label = (name: string, input?: Record<string, unknown>) => toolLabel(name, input, t, EPISODES);

describe("toolLabel", () => {
  it("names ArcReel tools in the UI language and refers to episodes by name, not by id", () => {
    expect(label("mcp__arcreel__generate_script_plan", { episode_id: 7 })).toMatchObject({
      name: "生成脚本规划",
      summary: "第 1 集",
    });
    expect(label("mcp__arcreel__get_episode_script", { script: "episode_3.json" }).summary).toBe("雨夜");
  });

  it("writes the episode once for item ids of the same episode and lists at most three", () => {
    expect(
      label("mcp__arcreel__generate_storyboards", { script: "episode_3.json", segment_ids: ["E3S01", "E3S02"] }).summary,
    ).toBe("雨夜 · S01、S02");
    expect(label("mcp__arcreel__inspect_video_units", { unit_ids: ["E3S01", "E3S02", "E3S03", "E3S04"] }).summary).toBe(
      "雨夜 · S01、S02、S03 等 4 个",
    );
  });

  it("keeps opaque ids such as timeline ids out of the summary", () => {
    expect(label("mcp__arcreel__read_timeline", { timeline: "tl-3f9a0c21" }).summary).toBe("");
    expect(label("mcp__arcreel__edit_timeline", { timeline: "tl-3f9a0c21", summary: "把推门镜头提前" }).summary).toBe(
      "把推门镜头提前",
    );
  });

  it("names built-in tools and falls back to truncated parameters for unknown tools", () => {
    expect(label("Read", { file_path: "/data/projects/demo/scripts/episode_3.json" })).toMatchObject({
      name: "读取文件",
      summary: "scripts/episode_3.json",
    });
    const unknown = label("NotebookEdit", { notebook_path: "x".repeat(200) });
    expect(unknown.name).toBe("NotebookEdit");
    expect(unknown.summary.endsWith("…")).toBe(true);
    expect(unknown.summary.length).toBeLessThan(200);
  });
});

describe("segmentWorkBlocks", () => {
  it("groups consecutive work blocks and keeps prose as separate segments", () => {
    const blocks: ContentBlock[] = [
      { type: "thinking", thinking: "先读剧本" },
      { type: "tool_use", id: "a", name: "Read" },
      { type: "text", text: "读完了" },
      { type: "tool_use", id: "b", name: "Bash" },
    ];
    expect(segmentWorkBlocks(blocks).map((segment) => [segment.work, segment.items.map((item) => item.index)])).toEqual([
      [true, [0, 1]],
      [false, [2]],
      [true, [3]],
    ]);
  });
});
