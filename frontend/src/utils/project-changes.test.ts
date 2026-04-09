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
    entity_id: "zhang-san",
    label: "Character「zhang-san」",
    important: true,
    focus: null,
    ...overrides,
  };
}

describe("project-changes utils", () => {
  it("groups changes by entity_type and action", () => {
    const groups = groupChangesByType([
      makeChange({ entity_id: "zhang-san", label: "Character「zhang-san」" }),
      makeChange({ entity_id: "li-si", label: "Character「li-si」" }),
      makeChange({
        entity_type: "clue",
        entity_id: "jade-pendant",
        label: "Clue「jade-pendant」",
      }),
      makeChange({
        entity_type: "character",
        action: "updated",
        entity_id: "wang-wu",
        label: "Character「wang-wu」",
      }),
    ]);

    expect(groups).toHaveLength(3);
    expect(groups[0]).toMatchObject({
      key: "character:created",
      changes: [expect.objectContaining({ entity_id: "zhang-san" }), expect.objectContaining({ entity_id: "li-si" })],
    });
    expect(groups[1].key).toBe("clue:created");
    expect(groups[2].key).toBe("character:updated");
  });

  it("formats grouped notification text and truncates long lists", () => {
    const [singleGroup] = groupChangesByType([
      makeChange({ entity_id: "zhang-san", label: "Character「zhang-san」" }),
    ]);
    expect(formatGroupedNotificationText(singleGroup)).toBe("Character「zhang-san」created");

    const [grouped] = groupChangesByType([
      makeChange({ entity_id: "zhang-san", label: "Character「zhang-san」" }),
      makeChange({ entity_id: "li-si", label: "Character「li-si」" }),
      makeChange({ entity_id: "wang-wu", label: "Character「wang-wu」" }),
      makeChange({ entity_id: "zhao-liu", label: "Character「zhao-liu」" }),
      makeChange({ entity_id: "qian-qi", label: "Character「qian-qi」" }),
      makeChange({ entity_id: "sun-ba", label: "Character「sun-ba」" }),
    ]);

    expect(formatGroupedNotificationText(grouped)).toBe(
      "Added 6 characters: zhang-san, li-si, wang-wu, zhao-liu, qian-qi...",
    );
    expect(formatGroupedDeferredText(grouped)).toBe(
      "AI just added 6 characters: zhang-san, li-si, wang-wu, zhao-liu, qian-qi..., click to view",
    );
  });
});
