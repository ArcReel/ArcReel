import { describe, expect, it } from "vitest";
import type { ProjectChange } from "@/types";
import {
  formatGroupedDeferredText,
  formatGroupedNotificationText,
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
  it("groups changes by entity_type and action", () => {
    const groups = groupChangesByType([
      makeChange({ entity_id: "张三", label: "角色「张三」" }),
      makeChange({ entity_id: "李四", label: "角色「李四」" }),
      makeChange({
        entity_type: "clue",
        entity_id: "玉佩",
        label: "线索「玉佩」",
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
    expect(groups[1].key).toBe("clue:created");
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
});
