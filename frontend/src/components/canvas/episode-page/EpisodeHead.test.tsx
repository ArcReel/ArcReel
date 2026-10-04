import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeCost, EpisodeMeta } from "@/types";

import { EpisodeHead } from "./EpisodeHead";

const FIRST: EpisodeMeta = { episode: 1, title: "第一章", script_file: "scripts/episode_1.json" };

function seedCost(projectName: string, actual: EpisodeCost["totals"]["actual"]) {
  const episodeCost: EpisodeCost = {
    episode: 1,
    title: "第一章",
    segments: [],
    totals: { estimate: { video: { USD: 10 } }, actual },
  };
  useCostStore.setState({
    costData: { project_name: projectName } as never,
    _episodeIndex: new Map([[1, episodeCost]]),
  });
}

function renderHead(meta: EpisodeMeta = FIRST, overrides: Partial<Parameters<typeof EpisodeHead>[0]> = {}) {
  return render(
    <EpisodeHead
      projectName="demo"
      episode={meta.episode}
      meta={meta}
      route="storyboard"
      canEditTitle
      onSaveTitle={vi.fn().mockResolvedValue(undefined)}
      canDelete
      {...overrides}
    />,
  );
}

describe("EpisodeHead", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useCostStore.setState(useCostStore.getInitialState(), true);
  });

  it("names an untitled episode by its position and lists its facts", () => {
    const blank: EpisodeMeta = {
      episode: 7,
      title: "",
      script_file: "",
      item_count: 12,
      duration_seconds: 125,
      videos: { available: 3, total: 12, stale: 0 },
    };
    useProjectsStore.setState({ currentProjectData: { episodes: [FIRST, blank] } as never });

    renderHead(blank);

    expect(screen.getByText("EP · 02")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第 2 集" })).toBeInTheDocument();
    expect(screen.getByText("12 分镜 · 时长 2:05 · 视频 3/12")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑分集标题" }));
    expect(screen.getByRole("textbox", { name: "编辑分集标题" })).toHaveAttribute("placeholder", "第 2 集");
  });

  it("counts history spend as spent but not against the remaining estimate", () => {
    seedCost("demo", { unassigned: { USD: 10 } });

    renderHead();

    // 已花含历史支出；剩余仍是整笔预估——那 $10 花在已被替换掉的旧剧本上，当前剧本的工作一件也没做
    expect(screen.getByText("预估 $10.00")).toBeInTheDocument();
    expect(screen.getByText("已花 $10.00，剩余 $10.00")).toBeInTheDocument();
  });

  it("deducts current-script spend from the remaining estimate", () => {
    seedCost("demo", { video: { USD: 4 } });

    renderHead();

    expect(screen.getByText("已花 $4.00，剩余 $6.00")).toBeInTheDocument();
  });

  it("leaves the cost out until this project's estimate has loaded", () => {
    seedCost("other-project", { video: { USD: 4 } });

    renderHead();

    expect(screen.queryByText(/预估/)).not.toBeInTheDocument();
  });

  it("offers no episode menu where episodes cannot be deleted", () => {
    renderHead(FIRST, { canDelete: false, canEditTitle: false });

    expect(screen.queryByRole("button", { name: "这一集的更多操作" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑分集标题" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
  });
});
