import { afterAll, beforeAll, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import type { ProjectChange } from "@/types";
import {
  formatGroupedDeferredText,
  formatGroupedNotificationText,
  GENERATION_ACTIONS,
  groupChangesByType,
  type EventsT,
} from "./project-changes";

let t: EventsT;

beforeAll(async () => {
  await i18n.loadNamespaces("events");
  t = i18n.getFixedT("zh", "events");
});

afterAll(async () => {
  await i18n.changeLanguage("zh");
});

function makeChange(overrides: Partial<ProjectChange> = {}): ProjectChange {
  return {
    entity_type: "character",
    action: "created",
    entity_id: "张三",
    label: "角色「张三」",
    label_key: "named_entity_character",
    label_params: { id: "张三" },
    important: true,
    focus: null,
    ...overrides,
  };
}

function namedCharacter(name: string): ProjectChange {
  return makeChange({
    entity_id: name,
    label: `角色「${name}」`,
    label_params: { id: name },
  });
}

describe("project-changes utils", () => {
  it("includes grid_ready in GENERATION_ACTIONS so grid completion refreshes cost", () => {
    expect(GENERATION_ACTIONS.has("grid_ready")).toBe(true);
  });

  it("groups changes by entity_type and action", () => {
    const groups = groupChangesByType([
      namedCharacter("张三"),
      namedCharacter("李四"),
      makeChange({
        entity_type: "prop",
        entity_id: "玉佩",
        label: "道具「玉佩」",
        label_key: "named_entity_prop",
        label_params: { id: "玉佩" },
      }),
      makeChange({
        entity_type: "character",
        action: "updated",
        entity_id: "王五",
        label: "角色「王五」",
        label_params: { id: "王五" },
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
    const [singleGroup] = groupChangesByType([namedCharacter("张三")]);
    expect(formatGroupedNotificationText(singleGroup, t)).toBe("角色「张三」已创建");

    const [grouped] = groupChangesByType(
      ["张三", "李四", "王五", "赵六", "钱七", "孙八"].map(namedCharacter),
    );

    expect(formatGroupedNotificationText(grouped, t)).toBe(
      "新增了 6 个角色：张三、李四、王五、赵六、钱七…等",
    );
    expect(formatGroupedDeferredText(grouped, t)).toBe(
      "AI 刚新增了 6 个角色：张三、李四、王五、赵六、钱七…等，点击查看",
    );
  });

  it("renders the label from label_key rather than the payload's default-language label", () => {
    // 后端 label 只是默认语言渲染结果，界面文案以稳定 key + 参数为准。
    const [group] = groupChangesByType([
      makeChange({
        entity_type: "grid",
        action: "grid_ready",
        entity_id: "E1G01",
        label: "后端兜底文案",
        label_key: "grid",
        label_params: { id: "E1G01" },
      }),
    ]);

    expect(formatGroupedNotificationText(group, t)).toBe("多宫格分镜「E1G01」已生成");
  });

  it("falls back to the payload label when the event carries no label_key", () => {
    const [group] = groupChangesByType([
      makeChange({
        entity_type: "grid",
        action: "grid_ready",
        entity_id: "E1G01",
        label: "旧发布方文案",
        label_key: undefined,
        label_params: undefined,
      }),
    ]);

    expect(formatGroupedNotificationText(group, t)).toBe("旧发布方文案已生成");
  });

  it("labels each skeleton kind's group title and item nouns consistently", () => {
    // 四种骨架 created 分组的标题名词须与条目名词一致：分镜/场景/镜头/视频单元。
    const cases: Array<{
      entityType: ProjectChange["entity_type"];
      labelKey: string;
      noun: string;
    }> = [
      { entityType: "segment", labelKey: "skeleton_segments", noun: "分镜" },
      { entityType: "drama_scene", labelKey: "skeleton_scenes", noun: "场景" },
      { entityType: "shot", labelKey: "skeleton_shots", noun: "镜头" },
      {
        entityType: "reference_unit",
        labelKey: "skeleton_video_units",
        noun: "视频单元",
      },
    ];

    for (const { entityType, labelKey, noun } of cases) {
      const [group] = groupChangesByType(
        ["E1X01", "E1X02"].map((id) =>
          makeChange({
            entity_type: entityType,
            action: "created",
            entity_id: id,
            label: `${noun}「${id}」`,
            label_key: labelKey,
            label_params: { id },
          }),
        ),
      );
      // 分组标题用 entity_type 名词，条目名单用裸 id（与既有 segment 行为一致）。
      expect(formatGroupedNotificationText(group, t)).toBe(
        `新增了 2 个${noun}：E1X01、E1X02`,
      );
    }
  });

  it("labels reference_unit notifications as 视频单元, not the 内容 fallback", () => {
    // 回归：后端曾把参考生视频任务完成事件的 entity_type 发成前端不认识的
    // "reference_video_unit"，落 entity 名词兜底显示「内容」。修复后 entity_type 与前端
    // 联合类型的 "reference_unit" 对齐，分组标题应显示「视频单元」。
    const [group] = groupChangesByType(
      ["U01", "U02"].map((id) =>
        makeChange({
          entity_type: "reference_unit",
          action: "reference_video_ready",
          entity_id: id,
          label: `参考视频「${id}」`,
          label_key: "reference_video",
          label_params: { id },
        }),
      ),
    );

    expect(formatGroupedNotificationText(group, t)).toBe(
      "已生成 2 个视频单元：U01、U02",
    );
    expect(formatGroupedNotificationText(group, t)).not.toContain("内容");
  });

  it("treats reference_video_ready/tts_ready as generation-completed, not the 更新了 fallback", () => {
    const [singleReferenceVideo] = groupChangesByType([
      makeChange({
        entity_type: "reference_unit",
        action: "reference_video_ready",
        entity_id: "U01",
        label: "参考视频「U01」",
        label_key: "reference_video",
        label_params: { id: "U01" },
      }),
    ]);
    expect(formatGroupedNotificationText(singleReferenceVideo, t)).toBe(
      "参考视频「U01」已生成",
    );
    expect(formatGroupedDeferredText(singleReferenceVideo, t)).toBe(
      "参考视频「U01」 已生成",
    );

    const ttsChange = (id: string) =>
      makeChange({
        entity_type: "segment",
        action: "tts_ready",
        entity_id: id,
        label: `旁白「${id}」`,
        label_key: "narration",
        label_params: { id },
      });

    const [singleTts] = groupChangesByType([ttsChange("E1S01")]);
    expect(formatGroupedNotificationText(singleTts, t)).toBe("旁白「E1S01」已生成");
    expect(formatGroupedDeferredText(singleTts, t)).toBe("旁白「E1S01」 已生成");

    const [groupedTts] = groupChangesByType([
      ttsChange("E1S01"),
      ttsChange("E1S02"),
    ]);
    expect(formatGroupedNotificationText(groupedTts, t)).toBe(
      "已生成 2 个旁白：E1S01、E1S02",
    );
    expect(formatGroupedNotificationText(groupedTts, t)).not.toContain("更新了");
  });

  it.each(["en", "vi"])(
    "renders every notification sentence in %s without Chinese leftovers",
    async (language) => {
      await i18n.changeLanguage(language);
      await i18n.loadNamespaces("events");
      const localized = i18n.getFixedT(null, "events");

      const groups = groupChangesByType([
        makeChange({ entity_id: "hero", label_params: { id: "hero" } }),
        makeChange({
          entity_type: "grid",
          action: "grid_ready",
          entity_id: "E1G01",
          label: "多宫格分镜「E1G01」",
          label_key: "grid",
          label_params: { id: "E1G01" },
        }),
        ...["E1S01", "E1S02"].map((id) =>
          makeChange({
            entity_type: "segment",
            action: "storyboard_ready",
            entity_id: id,
            label: `分镜「${id}」`,
            label_key: "skeleton_segments",
            label_params: { id },
          }),
        ),
        makeChange({
          entity_type: "episode",
          action: "created",
          entity_id: "1",
          label: "第 1 集",
          label_key: "episode",
          label_params: { episode: 1 },
        }),
      ]);

      const rendered = groups.flatMap((group) => [
        formatGroupedNotificationText(group, localized),
        formatGroupedDeferredText(group, localized),
      ]);

      expect(rendered).toHaveLength(8);
      for (const text of rendered) {
        expect(text).not.toMatch(/[一-鿿]/);
      }

      await i18n.changeLanguage("zh");
    },
  );
});
