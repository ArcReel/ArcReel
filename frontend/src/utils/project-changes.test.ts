import { describe, expect, it } from "vitest";
import type { ProjectChange } from "@/types";
import {
  formatGroupedDeferredText,
  formatGroupedNotificationText,
  GENERATION_ACTIONS,
  groupChangesByType,
} from "./project-changes";

function makeChange(overrides: Partial<ProjectChange> = {}): ProjectChange {
  return {
    entity_type: "character",
    action: "created",
    entity_id: "张三",
    label: "角色「张三」",
    important: true,
    focus: null,
    ...overrides,
  };
}

describe("project-changes utils", () => {
  it("includes grid_ready in GENERATION_ACTIONS so grid completion refreshes cost", () => {
    expect(GENERATION_ACTIONS.has("grid_ready")).toBe(true);
  });

  it("groups changes by entity_type and action", () => {
    const groups = groupChangesByType([
      makeChange({ entity_id: "张三", label: "角色「张三」" }),
      makeChange({ entity_id: "李四", label: "角色「李四」" }),
      makeChange({
        entity_type: "prop",
        entity_id: "玉佩",
        label: "道具「玉佩」",
      }),
      makeChange({
        entity_type: "character",
        action: "updated",
        entity_id: "王五",
        label: "角色「王五」",
      }),
    ]);

    expect(groups).toHaveLength(3);
    expect(groups[0]).toMatchObject({
      key: "character:created",
      changes: [expect.objectContaining({ entity_id: "张三" }), expect.objectContaining({ entity_id: "李四" })],
    });
    expect(groups[1].key).toBe("prop:created");
    expect(groups[2].key).toBe("character:updated");
  });

  it("formats grouped notification text and truncates long lists", () => {
    const [singleGroup] = groupChangesByType([
      makeChange({ entity_id: "张三", label: "角色「张三」" }),
    ]);
    expect(formatGroupedNotificationText(singleGroup)).toBe("角色「张三」已创建");

    const [grouped] = groupChangesByType([
      makeChange({ entity_id: "张三", label: "角色「张三」" }),
      makeChange({ entity_id: "李四", label: "角色「李四」" }),
      makeChange({ entity_id: "王五", label: "角色「王五」" }),
      makeChange({ entity_id: "赵六", label: "角色「赵六」" }),
      makeChange({ entity_id: "钱七", label: "角色「钱七」" }),
      makeChange({ entity_id: "孙八", label: "角色「孙八」" }),
    ]);

    expect(formatGroupedNotificationText(grouped)).toBe(
      "新增了 6 个角色：张三、李四、王五、赵六、钱七…等",
    );
    expect(formatGroupedDeferredText(grouped)).toBe(
      "AI 刚新增了 6 个角色：张三、李四、王五、赵六、钱七…等，点击查看",
    );
  });

  it("labels each skeleton kind's group title and item nouns consistently", () => {
    // 四种骨架 created 分组的标题名词须与条目名词一致：分镜/场景/镜头/视频单元。
    const cases: Array<{
      entityType: ProjectChange["entity_type"];
      noun: string;
    }> = [
      { entityType: "segment", noun: "分镜" },
      { entityType: "drama_scene", noun: "场景" },
      { entityType: "shot", noun: "镜头" },
      { entityType: "reference_unit", noun: "视频单元" },
    ];

    for (const { entityType, noun } of cases) {
      const [group] = groupChangesByType([
        makeChange({
          entity_type: entityType,
          action: "created",
          entity_id: "E1X01",
          label: `${noun}「E1X01」`,
        }),
        makeChange({
          entity_type: entityType,
          action: "created",
          entity_id: "E1X02",
          label: `${noun}「E1X02」`,
        }),
      ]);
      // 分组标题用 entity_type 名词，条目名单用裸 id（与既有 segment 行为一致）。
      expect(formatGroupedNotificationText(group)).toBe(
        `新增了 2 个${noun}：E1X01、E1X02`,
      );
    }
  });

  it("labels reference_unit notifications as 视频单元, not the 内容 fallback", () => {
    // 回归：后端曾把参考生视频任务完成事件的 entity_type 发成前端不认识的
    // "reference_video_unit"，落 ENTITY_LABELS 兜底显示「内容」。修复后 entity_type 与前端
    // 联合类型的 "reference_unit" 对齐，分组标题应显示「视频单元」。
    const [group] = groupChangesByType([
      makeChange({
        entity_type: "reference_unit",
        action: "reference_video_ready",
        entity_id: "U01",
        label: "参考视频「U01」",
      }),
      makeChange({
        entity_type: "reference_unit",
        action: "reference_video_ready",
        entity_id: "U02",
        label: "参考视频「U02」",
      }),
    ]);

    expect(formatGroupedNotificationText(group)).toBe(
      "已生成 2 个视频单元：U01、U02",
    );
    expect(formatGroupedNotificationText(group)).not.toContain("内容");
  });

  it("treats reference_video_ready/tts_ready as generation-completed, not the 更新了 fallback", () => {
    const [singleReferenceVideo] = groupChangesByType([
      makeChange({
        entity_type: "reference_unit",
        action: "reference_video_ready",
        entity_id: "U01",
        label: "参考视频「U01」",
      }),
    ]);
    expect(formatGroupedNotificationText(singleReferenceVideo)).toBe(
      "参考视频「U01」已生成",
    );
    expect(formatGroupedDeferredText(singleReferenceVideo)).toBe(
      "参考视频「U01」 已生成",
    );

    const [singleTts] = groupChangesByType([
      makeChange({
        entity_type: "segment",
        action: "tts_ready",
        entity_id: "E1S01",
        label: "旁白「E1S01」",
      }),
    ]);
    expect(formatGroupedNotificationText(singleTts)).toBe("旁白「E1S01」已生成");
    expect(formatGroupedDeferredText(singleTts)).toBe("旁白「E1S01」 已生成");

    const [groupedTts] = groupChangesByType([
      makeChange({
        entity_type: "segment",
        action: "tts_ready",
        entity_id: "E1S01",
        label: "旁白「E1S01」",
      }),
      makeChange({
        entity_type: "segment",
        action: "tts_ready",
        entity_id: "E1S02",
        label: "旁白「E1S02」",
      }),
    ]);
    expect(formatGroupedNotificationText(groupedTts)).toBe(
      "已生成 2 个旁白：E1S01、E1S02",
    );
    expect(formatGroupedNotificationText(groupedTts)).not.toContain("更新了");
  });
});
