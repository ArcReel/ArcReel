import { describe, expect, it } from "vitest";
import i18n from "@/i18n";
import {
  DEMO_PROJECT_NAME,
  DEMO_SCRIPTED_EPISODE,
  buildDemoProject,
  buildDemoProjectData,
  buildDemoScripts,
  isDemoProject,
} from "./demo-project";

const t = i18n.getFixedT("zh", "onboarding");

/** 图片字段一律是现算的 data URI —— 仓库里不落任何二进制占位图。 */
function expectInlinePlaceholder(value: string | null | undefined): void {
  expect(value).toMatch(/^data:image\/svg\+xml/);
}

describe("demo project data", () => {
  it("names the demo project something the backend can never accept", () => {
    // 后端 PROJECT_NAME_PATTERN 是 ^[A-Za-z0-9-]+$：下划线让同名真实项目无法存在
    expect(DEMO_PROJECT_NAME).toContain("_");
    expect(isDemoProject(DEMO_PROJECT_NAME)).toBe(true);
    expect(isDemoProject("some-project")).toBe(false);
    expect(isDemoProject(null)).toBe(false);
  });

  it("recognizes case-variant route params (wouter matches routes case-insensitively)", () => {
    expect(isDemoProject("ONBOARDING_DEMO")).toBe(true);
    expect(isDemoProject("Onboarding_Demo")).toBe(true);
  });

  it("keeps the lobby card and the workbench on the same summary", () => {
    const summary = buildDemoProject(t);
    const data = buildDemoProjectData(t);

    expect(summary.name).toBe(DEMO_PROJECT_NAME);
    expect(summary.title).toBe(data.title);
    expect(summary.status).toEqual(data.status);
  });

  it("matches the episode statuses against the summary counts", () => {
    const data = buildDemoProjectData(t);
    const summary = data.status?.episodes_summary;
    const count = (status: string) =>
      data.episodes.filter((e) => e.status === status).length;
    // scripted 统计的是"有生成剧本"的分集数（对齐 lib/workflow_state.py 的 script_status
    // 口径），不是 status 字段字面等于 "scripted" 的分集数——没剧本的分集永远只能是 draft
    const scriptedCount = data.episodes.filter((e) => e.script_file !== "").length;

    expect(data.episodes).toHaveLength(summary?.total ?? 0);
    expect(scriptedCount).toBe(summary?.scripted);
    expect(count("in_production")).toBe(summary?.in_production);
    expect(count("completed")).toBe(summary?.completed);
  });

  it("gives asset counts that agree with the generated sheets", () => {
    const data = buildDemoProjectData(t);
    const status = data.status!;
    const characters = Object.values(data.characters);
    const scenes = Object.values(data.scenes ?? {});
    const props = Object.values(data.props ?? {});

    expect(characters).toHaveLength(status.assets.character.total);
    expect(characters.filter((c) => c.character_sheet).length).toBe(
      status.assets.character.available,
    );
    expect(props).toHaveLength(status.assets.prop.total);
    expect(props.filter((p) => p.prop_sheet).length).toBe(status.assets.prop.available);
    expect(scenes).toHaveLength(status.assets.scene.total);
    expect(scenes.filter((s) => s.scene_sheet).length).toBe(status.assets.scene.available);
  });

  it("scripts only the first episode", () => {
    const data = buildDemoProjectData(t);
    const scripted = data.episodes.filter((e) => e.script_file !== "");

    expect(scripted).toHaveLength(1);
    expect(scripted[0].episode).toBe(DEMO_SCRIPTED_EPISODE);
    expect(data.episodes.every((e) => e.title.length > 0)).toBe(true);
  });

  it("resolves every string through the onboarding namespace", () => {
    const data = buildDemoProjectData(t);
    const text = JSON.stringify([data, buildDemoScripts(t)]);

    // 未命中的 key 会被 i18next 原样回显，扫一遍就能发现漏翻的字段
    expect(text).not.toMatch(/demo_(overview|episode|character|scene|prop|shot)_/);
  });

  it("references only assets that exist in the lorebook", () => {
    const data = buildDemoProjectData(t);
    const segments = buildDemoScripts(t)["E1.json"].segments;

    for (const segment of segments) {
      for (const name of segment.characters_in_segment) {
        expect(Object.keys(data.characters)).toContain(name);
      }
      for (const name of segment.scenes ?? []) {
        expect(Object.keys(data.scenes ?? {})).toContain(name);
      }
      for (const name of segment.props ?? []) {
        expect(Object.keys(data.props ?? {})).toContain(name);
      }
    }
  });

  it("counts shots and storyboards the way the episode meta claims", () => {
    const data = buildDemoProjectData(t);
    const episode = data.episodes.find((e) => e.episode === DEMO_SCRIPTED_EPISODE)!;
    const segments = buildDemoScripts(t)["E1.json"].segments;
    const withStoryboard = segments.filter(
      (s) => s.generated_assets?.storyboard_image,
    );

    expect(segments.length).toBe(episode.scenes_count);
    expect(segments.length).toBeGreaterThanOrEqual(6);
    expect(withStoryboard.length).toBe(episode.storyboards?.available);
    expect(episode.videos?.available).toBe(0);
  });

  it("leaves video and narration unset — an SVG cannot stand in for them", () => {
    const segments = buildDemoScripts(t)["E1.json"].segments;

    for (const segment of segments) {
      expect(segment.generated_assets?.video_clip).toBeNull();
      expect(segment.generated_assets?.video_thumbnail).toBeNull();
      expect(segment.generated_assets?.narration_audio).toBeNull();
    }
  });

  it("draws every placeholder inline as an SVG data URI", () => {
    const data = buildDemoProjectData(t);
    const segments = buildDemoScripts(t)["E1.json"].segments;

    for (const character of Object.values(data.characters)) {
      if (character.character_sheet) expectInlinePlaceholder(character.character_sheet);
    }
    for (const scene of Object.values(data.scenes ?? {})) {
      expectInlinePlaceholder(scene.scene_sheet);
    }
    for (const prop of Object.values(data.props ?? {})) {
      if (prop.prop_sheet) expectInlinePlaceholder(prop.prop_sheet);
    }
    for (const segment of segments) {
      const image = segment.generated_assets?.storyboard_image;
      if (image) expectInlinePlaceholder(image);
    }
  });
});
