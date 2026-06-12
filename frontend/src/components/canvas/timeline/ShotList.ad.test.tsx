import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotList } from "./ShotList";
import type { AdShot } from "@/types";

// jsdom 中滚动容器无高度，真实 virtualizer 渲染 0 行；mock 成全量渲染以断言行内容
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 96,
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, start: index * 96 })),
    measureElement: () => {},
  }),
}));

function makeShot(overrides: Partial<AdShot> = {}): AdShot {
  return {
    shot_id: "E1S01",
    section: "hook",
    duration_seconds: 4,
    voiceover_text: "还在等杯子干？",
    image_prompt: {
      scene: "速干杯特写",
      composition: { shot_type: "Close-up", lighting: "顶光", ambiance: "清爽" },
    },
    video_prompt: {
      action: "水珠滑落",
      camera_motion: "Static",
      ambiance_audio: "水声",
      dialogue: [],
    },
    transition_to_next: "cut",
    ...overrides,
  };
}

function renderList(shots: AdShot[]) {
  return render(
    <ShotList
      segments={shots}
      selectedIndex={0}
      onSelect={vi.fn()}
      contentMode="ad"
      projectName="demo"
      collapsed={false}
      onToggleCollapse={vi.fn()}
    />,
  );
}

describe("ShotList ad 模式", () => {
  it("列表预览展示口播文案与 section 标签", () => {
    renderList([makeShot()]);
    expect(screen.getByText("还在等杯子干？")).toBeInTheDocument();
    expect(screen.getByText("hook")).toBeInTheDocument();
  });

  it("无口播的纯画面镜头退回画面描述", () => {
    renderList([makeShot({ voiceover_text: "" })]);
    expect(screen.getByText("速干杯特写")).toBeInTheDocument();
  });
});
