import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EpisodeCost, EpisodeMeta } from "@/types";
import { EpisodeHeader } from "./EpisodeHeader";

const EP: EpisodeMeta = { episode: 1, title: "Ep1", script_file: "episode_1.json" };

function episodeCost(actual: EpisodeCost["totals"]["actual"]): EpisodeCost {
  return {
    episode: 1,
    title: "Ep1",
    segments: [],
    totals: { estimate: { video: { USD: 10 } }, actual },
  };
}

describe("EpisodeHeader", () => {
  it("reads progress from storyboard_count on the storyboard route", () => {
    render(
      <EpisodeHeader
        ep={{
          ...EP,
          status: "in_production",
          storyboard_count: 4,
          videos: { total: 4, completed: 1 },
        }}
        segmentCount={1}
        totalDuration={5}
      />,
    );

    expect(screen.getByText(/25%/)).toBeInTheDocument();
  });

  it("falls back to video_unit_count on the reference route", () => {
    render(
      <EpisodeHeader
        ep={{
          ...EP,
          status: "in_production",
          video_unit_count: 2,
          videos: { total: 2, completed: 1 },
        }}
        segmentCount={1}
        totalDuration={5}
      />,
    );

    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  it("shows zero progress when neither count is injected", () => {
    render(
      <EpisodeHeader
        ep={{ ...EP, status: "in_production", videos: { total: 0, completed: 3 } }}
        segmentCount={1}
        totalDuration={5}
      />,
    );

    expect(screen.getByText(/0%/)).toBeInTheDocument();
  });


  it("counts history spend as spent but not against the remaining estimate", () => {
    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={1}
        totalDuration={5}
        episodeCost={episodeCost({ unassigned: { USD: 10 } })}
      />,
    );

    // 已花含历史支出；剩余仍是整笔预估——那 $10 花在已被替换掉的旧剧本上，
    // 当前剧本的工作一件也没做。
    expect(screen.getAllByText("$10.00")).toHaveLength(3);
  });

  it("deducts current-script spend from the remaining estimate", () => {
    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={1}
        totalDuration={5}
        episodeCost={episodeCost({ video: { USD: 4 } })}
      />,
    );

    expect(screen.getByText("$4.00")).toBeInTheDocument();
    expect(screen.getByText("$6.00")).toBeInTheDocument();
  });
});
