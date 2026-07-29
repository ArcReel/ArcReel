import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useCostStore } from "@/stores/cost-store";
import type { CostByType } from "@/types";
import { EpisodeHeader } from "./EpisodeHeader";

const UNITS = [{ duration_seconds: 5, generated_assets: { video_clip: "a.mp4" } }];

function seedEpisodeCost(actual: CostByType) {
  useCostStore.setState({
    _episodeIndex: new Map([
      [
        1,
        {
          episode: 1,
          title: "EP1",
          segments: [],
          totals: { estimate: { video: { USD: 8 } }, actual },
        },
      ],
    ]),
  });
}

afterEach(() => {
  useCostStore.setState(useCostStore.getInitialState(), true);
});

describe("reference EpisodeHeader", () => {
  it("counts history and audio spend in the episode actual", () => {
    seedEpisodeCost({ video: { USD: 2 }, audio: { USD: 0.5 }, unassigned: { USD: 1.25 } });

    render(<EpisodeHeader episode={1} title="EP1" units={UNITS} />);

    expect(screen.getByText("$3.75")).toBeInTheDocument();
  });

  it("shows history spend even when the current units have none", () => {
    seedEpisodeCost({ unassigned: { USD: 1.25 } });

    render(<EpisodeHeader episode={1} title="EP1" units={UNITS} />);

    expect(screen.getByText("$1.25")).toBeInTheDocument();
  });
});
