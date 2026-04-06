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
    entity_id: "Trương Tam",
    label: "Nhân vật「Trương Tam」",
    important: true,
    focus: null,
    ...overrides,
  };
}

describe("project-changes utils", () => {
  it("groups changes by entity_type and action", () => {
    const groups = groupChangesByType([
      makeChange({ entity_id: "Trương Tam", label: "Nhân vật「Trương Tam」" }),
      makeChange({ entity_id: "Lý Tứ", label: "Nhân vật「Lý Tứ」" }),
      makeChange({
        entity_type: "clue",
        entity_id: "Ngọc bội",
        label: "Manh mối「Ngọc bội」",
      }),
      makeChange({
        entity_type: "character",
        action: "updated",
        entity_id: "Vương Ngũ",
        label: "Nhân vật「Vương Ngũ」",
      }),
    ]);

    expect(groups).toHaveLength(3);
    expect(groups[0]).toMatchObject({
      key: "character:created",
      changes: [expect.objectContaining({ entity_id: "Trương Tam" }), expect.objectContaining({ entity_id: "Lý Tứ" })],
    });
    expect(groups[1].key).toBe("clue:created");
    expect(groups[2].key).toBe("character:updated");
  });

  it("formats grouped notification text and truncates long lists", () => {
    const [singleGroup] = groupChangesByType([
      makeChange({ entity_id: "Trương Tam", label: "Nhân vật「Trương Tam」" }),
    ]);
    expect(formatGroupedNotificationText(singleGroup)).toBe("Nhân vật「Trương Tam」 đã được tạo");

    const [grouped] = groupChangesByType([
      makeChange({ entity_id: "Trương Tam", label: "Nhân vật「Trương Tam」" }),
      makeChange({ entity_id: "Lý Tứ", label: "Nhân vật「Lý Tứ」" }),
      makeChange({ entity_id: "Vương Ngũ", label: "Nhân vật「Vương Ngũ」" }),
      makeChange({ entity_id: "Triệu Lục", label: "Nhân vật「Triệu Lục」" }),
      makeChange({ entity_id: "Tiền Thất", label: "Nhân vật「Tiền Thất」" }),
      makeChange({ entity_id: "Tôn Bát", label: "Nhân vật「Tôn Bát」" }),
    ]);

    expect(formatGroupedNotificationText(grouped)).toBe(
      "Đã thêm 6 Nhân vật: Trương Tam, Lý Tứ, Vương Ngũ, Triệu Lục, Tiền Thất…v.v.",
    );
    expect(formatGroupedDeferredText(grouped)).toBe(
      "AI vừa thêm 6 Nhân vật: Trương Tam, Lý Tứ, Vương Ngũ, Triệu Lục, Tiền Thất…v.v., nhấn để xem",
    );
  });
});
