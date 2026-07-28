import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { EpisodeSourceReview } from "./EpisodeSourceReview";
import type { EpisodeMeta } from "@/types";

function makeEpisode(overrides: Partial<EpisodeMeta> = {}): EpisodeMeta {
  return {
    episode: 1,
    title: "第一章：初遇",
    script_file: "scripts/episode_1.json",
    source_range: { source_file: "source/episode_1.txt", start: 100, end: 340 },
    outline: { story_beats: ["主角登场", "遭遇冲突"] },
    hook: "反派现身",
    ...overrides,
  };
}

describe("EpisodeSourceReview", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("renders header meta, guide beats/hook, and the loaded source text", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("这是本集源文……");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByText("第一章：初遇")).toBeInTheDocument();
    expect(screen.getByText("剧本未生成")).toBeInTheDocument();
    expect(screen.getByText("episode_1.txt")).toBeInTheDocument();
    expect(screen.getByText("100–340")).toBeInTheDocument();
    expect(screen.getByText("约 240 字")).toBeInTheDocument();
    expect(screen.getByText("主角登场")).toBeInTheDocument();
    expect(screen.getByText("遭遇冲突")).toBeInTheDocument();
    expect(screen.getByText("反派现身")).toBeInTheDocument();

    expect(screen.getByText("正在加载源文切片…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("这是本集源文……")).toBeInTheDocument();
    });
    expect(API.getSourceContent).toHaveBeenCalledWith("demo", "episode_1.txt");
  });

  it("shows a not-found message when the source slice fetch fails", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));

    render(
      <EpisodeSourceReview
        projectName="demo"
        episode={2}
        episodes={[makeEpisode({ episode: 2, outline: undefined, hook: undefined, source_range: undefined })]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未找到本集源文切片（source/episode_2.txt）")).toBeInTheDocument();
    });
  });

  it("does not render the guide section when there are no beats or hook", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");

    render(
      <EpisodeSourceReview
        projectName="demo"
        episode={3}
        episodes={[makeEpisode({ episode: 3, outline: undefined, hook: undefined })]}
      />,
    );

    expect(screen.queryByText("本集导览")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("text")).toBeInTheDocument();
    });
  });

  it("collapses and re-expands the guide section", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByText("主角登场")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.queryByText("主角登场")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.getByText("主角登场")).toBeInTheDocument();
  });

  it("resets the collapsed guide section when switching to a different episode", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");
    const episodes = [makeEpisode(), makeEpisode({ episode: 2, outline: { story_beats: ["新的一集"] } })];

    const { rerender } = render(
      <EpisodeSourceReview projectName="demo" episode={1} episodes={episodes} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.queryByText("主角登场")).not.toBeInTheDocument();

    rerender(<EpisodeSourceReview projectName="demo" episode={2} episodes={episodes} />);

    expect(screen.getByText("新的一集")).toBeInTheDocument();
  });

  it("prefills the assistant input and opens the panel on CTA click", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");
    useAppStore.setState({ assistantPanelOpen: false });

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    fireEvent.click(screen.getByRole("button", { name: "开始创作 E1" }));

    expect(useAssistantStore.getState().input).toBe("为第 1 集生成剧本");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);

    await waitFor(() => {
      expect(screen.getByText("text")).toBeInTheDocument();
    });
  });
});
