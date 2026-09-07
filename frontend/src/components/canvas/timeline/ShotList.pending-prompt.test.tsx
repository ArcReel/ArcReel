import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotList } from "./ShotList";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

// jsdom 中滚动容器无高度，真实 virtualizer 渲染 0 行；mock 成全量渲染以断言行内容
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 96,
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, start: index * 96 })),
    measureElement: () => {},
  }),
}));

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

describe("ShotList 待生成提示词", () => {
  it("任一侧提示词为 null 的条目标「提示词待生成」", () => {
    renderList([
      makeNarrationSegment(),
      makeNarrationSegment({ segment_id: "E1S02", image_prompt: null }),
      makeNarrationSegment({ segment_id: "E1S03", video_prompt: null }),
    ]);
    expect(screen.getAllByText("提示词待生成")).toHaveLength(2);
  });

  it("提示词齐全时不标记", () => {
    renderList([makeNarrationSegment()]);
    expect(screen.queryByText("提示词待生成")).not.toBeInTheDocument();
  });
});
