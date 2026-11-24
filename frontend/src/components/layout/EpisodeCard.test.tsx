import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EpisodeCard } from "./EpisodeCard";
import type { EpisodeMeta } from "@/types/project";
import type { GenerationRoute } from "@/utils/generation-mode";

function makeEpisode(overrides: Partial<EpisodeMeta> = {}): EpisodeMeta {
  return {
    episode: 1,
    title: "第一集",
    script_file: "scripts/episode_1.json",
    script_status: "generated",
    status: "in_production",
    item_count: 4,
    duration_seconds: 96,
    storyboards: { total: 4, available: 4, stale: 0 },
    videos: { total: 4, available: 3, stale: 0 },
    ...overrides,
  };
}

function renderCard(ep: EpisodeMeta, route: GenerationRoute = "storyboard") {
  return render(
    <EpisodeCard ep={ep} position={1} active={false} onClick={() => {}} route={route} />,
  );
}

describe("EpisodeCard", () => {
  it("reports available videos against the total, not the script item count", () => {
    renderCard(makeEpisode({ item_count: 9 }));

    expect(screen.getByText(/3\/4/)).toBeInTheDocument();
  });

  it("counts stale artifacts across storyboards and videos without deducting them from available", () => {
    renderCard(
      makeEpisode({
        storyboards: { total: 4, available: 4, stale: 2 },
        videos: { total: 4, available: 4, stale: 1 },
      }),
    );

    expect(screen.getByText(/4\/4/)).toBeInTheDocument();
    expect(screen.getByText("3 件产物比当前内容旧")).toBeInTheDocument();
  });

  it("says nothing about staleness when every artifact matches the current content", () => {
    renderCard(makeEpisode());

    expect(screen.queryByText(/比当前内容旧/)).not.toBeInTheDocument();
  });

  it("falls back to the script item count for an episode with no videos planned yet", () => {
    renderCard(unplannedEpisode());

    expect(screen.getByText(/^4 /)).toBeInTheDocument();
    expect(screen.queryByText(/0\/0/)).not.toBeInTheDocument();
  });

  it("names the fallback count 分镜数 on the storyboard route", () => {
    renderCard(unplannedEpisode(), "storyboard");

    expect(screen.getByText(/^4 分镜/)).toBeInTheDocument();
  });

  it("names the fallback count 视频单元数 on the reference route", () => {
    renderCard(unplannedEpisode(), "reference_video");

    expect(screen.getByText(/^4 视频单元/)).toBeInTheDocument();
  });
});

function unplannedEpisode(): EpisodeMeta {
  return makeEpisode({
    status: "scripted",
    storyboards: { total: 0, available: 0, stale: 0 },
    videos: { total: 0, available: 0, stale: 0 },
  });
}
