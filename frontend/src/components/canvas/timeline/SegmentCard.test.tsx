import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SegmentCard } from "./SegmentCard";
import { useAppStore } from "@/stores/app-store";
import type { NarrationSegment } from "@/types";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));

vi.mock("@/components/ui/AvatarStack", () => ({
  AvatarStack: () => <div data-testid="avatar-stack">avatars</div>,
}));

vi.mock("@/components/ui/ImageFlipReveal", () => ({
  ImageFlipReveal: ({
    src,
    alt,
    className,
    fallback,
  }: {
    src: string | null;
    alt: string;
    className?: string;
    fallback?: ReactNode;
  }) =>
    src ? <img src={src} alt={alt} className={className} /> : <>{fallback}</>,
}));

function makeSegment(overrides: Partial<NarrationSegment> = {}): NarrationSegment {
  return {
    segment_id: "SEG-1",
    episode: 1,
    duration_seconds: 4,
    segment_break: false,
    novel_text: "在雨夜里抬头。",
    characters_in_segment: ["Hero"],
    scenes: [],
    props: [],
    image_prompt: "一张电影感分镜图",
    video_prompt: "镜头缓慢推进",
    transition_to_next: "cut",
    generated_assets: {
      storyboard_image: "storyboards/SEG-1.png",
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: "videos/SEG-1.mp4",
      video_thumbnail: null,
      video_uri: null,
      status: "completed",
    },
    ...overrides,
  };
}

describe("SegmentCard", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows an image fullscreen trigger and uses native video controls", () => {
    const { container } = render(
      <SegmentCard
        segment={makeSegment()}
        contentMode="narration"
        aspectRatio="16:9"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="demo"
      />,
    );

    expect(
      screen.getByRole("button", { name: "SEG-1 分镜图 全屏预览" }),
    ).toBeInTheDocument();

    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute("controls");
    expect(video).toHaveAttribute("preload", "metadata");
  }, 10_000);
});

describe("SegmentCard — duration incompatible warning", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("当 segment.duration_seconds 不在 durationOptions 内时显示 ⚠ 角标（只读模式）", () => {
    render(
      <SegmentCard
        segment={makeSegment({ duration_seconds: 6 })}
        contentMode="narration"
        aspectRatio="9:16"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="p"
        durationOptions={[4, 8, 12]}
      />,
    );
    const warning = screen.getByLabelText(/不在模型支持范围|not in model-supported/i);
    expect(warning).toBeInTheDocument();
  });

  it("当 segment.duration_seconds 在 durationOptions 内时不显示角标", () => {
    render(
      <SegmentCard
        segment={makeSegment({ duration_seconds: 4 })}
        contentMode="narration"
        aspectRatio="9:16"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="p"
        durationOptions={[4, 8, 12]}
      />,
    );
    expect(
      screen.queryByLabelText(/不在模型支持范围|not in model-supported/i),
    ).not.toBeInTheDocument();
  });

  it("durationOptions 为空（无 supported_durations 信号）时不显示角标", () => {
    render(
      <SegmentCard
        segment={makeSegment({ duration_seconds: 6 })}
        contentMode="narration"
        aspectRatio="9:16"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="p"
      />,
    );
    expect(
      screen.queryByLabelText(/不在模型支持范围|not in model-supported/i),
    ).not.toBeInTheDocument();
  });
});
