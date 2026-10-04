import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotList } from "./ShotList";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

function renderList(segments: NarrationSegment[]) {
  return render(
    <ShotList
      segments={segments}
      selectedIndex={0}
      onSelect={vi.fn()}
      contentMode="narration"
      projectName="demo"
      collapsed={false}
      onToggleCollapse={vi.fn()}
    />,
  );
}

describe("ShotList 待编写徽标", () => {
  it("pending_authoring 为真的条目标「待编写」", () => {
    renderList([
      makeNarrationSegment(),
      makeNarrationSegment({ segment_id: "E1S02", pending_authoring: true }),
      makeNarrationSegment({ segment_id: "E1S03", pending_authoring: false }),
    ]);
    expect(screen.getAllByText("待编写")).toHaveLength(1);
  });

  it("提示词为 null 但 pending_authoring 为假的历史形态不标记", () => {
    renderList([makeNarrationSegment({ image_prompt: null, video_prompt: null })]);
    expect(screen.queryByText("待编写")).not.toBeInTheDocument();
  });
});
