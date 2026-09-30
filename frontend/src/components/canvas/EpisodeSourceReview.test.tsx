import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
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
    vi.restoreAllMocks();
  });

  it("renders header meta, guide beats/hook, and the loaded source text", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("这是本集源文……");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByText("第一章：初遇")).toBeInTheDocument();
    expect(screen.getByText("脚本未生成")).toBeInTheDocument();
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

  it("shows a not-found message when a cut episode's source slice fetch fails", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));

    render(
      <EpisodeSourceReview
        projectName="demo"
        episode={2}
        episodes={[makeEpisode({ episode: 2, outline: undefined, hook: undefined, source_origin: "whole_source" })]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("本集没有集原文，可以从空白开始手写脚本")).toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps a cut episode's source read-only", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("切出的原文");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    await waitFor(() => expect(screen.getByText("切出的原文")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "编辑集原文" })).not.toBeInTheDocument();
  });

  it("lets a no-source episode fill in its source and refreshes the project after saving", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));
    const save = vi.spyOn(API, "updateEpisodeSource").mockResolvedValue({ success: true });
    const refresh = vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    const noSource = makeEpisode({ episode: 4, source_origin: "none", source_range: undefined });

    render(<EpisodeSourceReview projectName="demo" episode={4} episodes={[noSource]} />);

    const box = await screen.findByRole("textbox", { name: "填写或粘贴本集的集原文" });
    expect(screen.getByRole("button", { name: "保存集原文" })).toBeDisabled();

    fireEvent.change(box, { target: { value: "粘贴进来的本集原文" } });
    fireEvent.click(screen.getByRole("button", { name: "保存集原文" }));

    await waitFor(() => expect(screen.getByText("粘贴进来的本集原文")).toBeInTheDocument());
    expect(save).toHaveBeenCalledWith("demo", 4, "粘贴进来的本集原文");
    expect(refresh).toHaveBeenCalledWith("demo");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps the draft and reports the error when saving fails", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));
    vi.spyOn(API, "updateEpisodeSource").mockRejectedValue(new Error("磁盘已满"));
    const noSource = makeEpisode({ episode: 4, source_origin: "none", source_range: undefined });

    render(<EpisodeSourceReview projectName="demo" episode={4} episodes={[noSource]} />);

    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存集原文" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("集原文保存失败：磁盘已满"));
    expect(screen.getByRole("textbox")).toHaveValue("草稿");
  });

  it("lets an own-source episode edit its source and cancel back to reading", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const own = makeEpisode({ episode: 6, source_origin: "own", source_range: undefined });

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[own]} />);

    fireEvent.click(await screen.findByRole("button", { name: "编辑集原文" }));
    expect(screen.getByRole("textbox")).toHaveValue("自带的原文");

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("自带的原文")).toBeInTheDocument();
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
});
