import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useEpisodeSurfaceStore } from "@/stores/episode-surface-store";
import { useProjectsStore } from "@/stores/projects-store";
import { EpisodeSourceReview } from "./EpisodeSourceReview";
import type { EpisodeMeta } from "@/types";
import type { EpisodeSourceWriteResult } from "@/types/episodes-view";

function written(overrides: Partial<EpisodeSourceWriteResult> = {}): EpisodeSourceWriteResult {
  return {
    success: true,
    episode: 4,
    source_origin: "own",
    applied: true,
    needs_confirmation: false,
    affected_episodes: [],
    ...overrides,
  };
}

function makeEpisode(overrides: Partial<EpisodeMeta> = {}): EpisodeMeta {
  return {
    episode: 1,
    title: "第一章：初遇",
    script_file: "",
    source_origin: "whole_source",
    source_range: { source_file: "source/episode_1.txt", start: 100, end: 340 },
    outline: { story_beats: ["主角登场", "遭遇冲突"] },
    hook: "反派现身",
    ...overrides,
  };
}

const NO_SOURCE = makeEpisode({ episode: 4, source_origin: "none", source_range: undefined });
const OWN = makeEpisode({ episode: 6, title: "番外", source_origin: "own", source_range: undefined, source_kind: "novel" });

function useDramaProject() {
  useProjectsStore.setState({
    currentProjectData: { title: "Demo", content_mode: "drama", style: "", episodes: [], characters: {} },
  });
}

const sourceBox = () => screen.findByRole("textbox", { name: "本集原文" });

describe("EpisodeSourceReview", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useEpisodeSurfaceStore.setState({ request: null });
    useAssistantStore.getState().setInput("");
    vi.restoreAllMocks();
  });

  it("puts the starter above a cut episode's read-only source and points edits to the Episodes view", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("这是本集源文……");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByRole("heading", { name: "这一集还没有脚本" })).toBeInTheDocument();
    const source = screen.getByRole("region", { name: "本集原文" });
    expect(within(source).getByText("episode_1.txt")).toBeInTheDocument();
    expect(within(source).getByText("100–340")).toBeInTheDocument();
    expect(within(source).getByText("约 240 字")).toBeInTheDocument();
    expect(within(source).getByRole("status")).toHaveTextContent("正在加载源文切片…");

    expect(await within(source).findByText("这是本集源文……")).toBeInTheDocument();
    expect(API.getSourceContent).toHaveBeenCalledWith("demo", "episode_1.txt", { signal: expect.any(AbortSignal) });
    expect(within(source).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(source).getByRole("link", { name: "去分集" })).toHaveAttribute("href", "/episodes?episode=1");
  });

  it("shows only the start and end files for an episode that spans two source files", () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("跨文件的原文");
    const spanning = makeEpisode({
      source_range: { source_file: "source/part_1.txt", start: 900, end: 120, end_file: "source/part_2.txt" },
    });

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[spanning]} />);

    expect(screen.getByText("part_1.txt – part_2.txt")).toBeInTheDocument();
    expect(screen.queryByText("900–120")).not.toBeInTheDocument();
    expect(screen.queryByText(/约 .* 字/)).not.toBeInTheDocument();
  });

  it("says the source is missing when a cut episode's slice can't be read", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));

    render(<EpisodeSourceReview projectName="demo" episode={2} episodes={[makeEpisode({ episode: 2 })]} />);

    expect(await screen.findByText("本集没有集原文，可以从空白开始手写脚本")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "本集原文" })).not.toBeInTheDocument();
  });

  it("lets a no-source episode fill in its source in place without reading a same-name file", async () => {
    const read = vi.spyOn(API, "getSourceContent").mockResolvedValue("手放进 source/ 的同名文件");
    const save = vi.spyOn(API, "updateEpisodeSource").mockResolvedValue(written());
    const refresh = vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");

    render(<EpisodeSourceReview projectName="demo" episode={4} episodes={[NO_SOURCE]} />);

    const box = await sourceBox();
    expect(box).toHaveValue("");
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "去分集" })).not.toBeInTheDocument();

    fireEvent.change(box, { target: { value: "粘贴进来的本集原文" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(refresh).toHaveBeenCalledWith("demo", undefined));
    expect(save).toHaveBeenCalledWith("demo", 4, "粘贴进来的本集原文", undefined, false);
    expect(screen.getByRole("textbox", { name: "本集原文" })).toHaveValue("粘贴进来的本集原文");
  });

  it("keeps the saved source and warns when the refresh after saving fails", async () => {
    vi.spyOn(API, "updateEpisodeSource").mockResolvedValue(written());
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));

    render(<EpisodeSourceReview projectName="demo" episode={4} episodes={[NO_SOURCE]} />);

    fireEvent.change(await sourceBox(), { target: { value: "粘贴进来的本集原文" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      }),
    );
    expect(screen.getByRole("textbox", { name: "本集原文" })).toHaveValue("粘贴进来的本集原文");
  });

  it("refuses to save an emptied source", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const save = vi.spyOn(API, "updateEpisodeSource");

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);

    fireEvent.change(await sourceBox(), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("集原文不能为空");
    expect(save).not.toHaveBeenCalled();
  });

  it("saves the edited source before AI planning, so the plan reads what is on screen", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    const order: string[] = [];
    const save = vi.spyOn(API, "updateEpisodeSource").mockImplementation(async () => {
      order.push("save");
      return written({ episode: 6 });
    });
    const plan = vi.spyOn(API, "planScript").mockImplementation(async () => {
      order.push("plan");
      return { batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>;
    });

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);
    fireEvent.change(await sourceBox(), { target: { value: "改过的本集原文" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 规划" }));

    await waitFor(() => expect(plan).toHaveBeenCalledWith("demo", 6, { instructions: null }));
    expect(save).toHaveBeenCalledWith("demo", 6, "改过的本集原文", undefined, false);
    expect(order).toEqual(["save", "plan"]);
  });

  it("holds the source still while it saves for planning, so the plan reads what is on screen", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    let finishSave: (result: EpisodeSourceWriteResult) => void = () => {};
    vi.spyOn(API, "updateEpisodeSource").mockImplementation(() => new Promise((resolve) => { finishSave = resolve; }));
    const plan = vi
      .spyOn(API, "planScript")
      .mockResolvedValue({ batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.planScript>>);

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);
    const box = await sourceBox();
    fireEvent.change(box, { target: { value: "改过的本集原文" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 规划" }));
    await userEvent.type(box, "，又补一句");
    expect(box).toHaveValue("改过的本集原文");

    await act(async () => finishSave(written({ episode: 6 })));
    await waitFor(() => expect(plan).toHaveBeenCalledTimes(1));
  });

  it("hands nothing to the Agent when the edited source fails to save", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const saveInstructions = vi.spyOn(API, "saveScriptPlanInstructions").mockResolvedValue({ success: true });

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);
    fireEvent.change(await sourceBox(), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "交给 Agent" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("集原文不能为空");
    expect(saveInstructions).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().input).toBe("");
  });

  it("records the chosen source kind when a drama episode saves its source", async () => {
    useDramaProject();
    const save = vi.spyOn(API, "updateEpisodeSource").mockResolvedValue(written());
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");

    render(<EpisodeSourceReview projectName="demo" episode={4} episodes={[NO_SOURCE]} />);

    fireEvent.change(await sourceBox(), { target: { value: "剧本原文" } });
    expect(screen.getByRole("combobox", { name: "源文件类型" })).toHaveTextContent("小说");
    await userEvent.click(screen.getByRole("combobox", { name: "源文件类型" }));
    await userEvent.click(await screen.findByRole("option", { name: /^剧本/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("demo", 4, "剧本原文", "screenplay", false));
  });

  it("confirms before a kind change stales this episode's script plan, then saves", async () => {
    useDramaProject();
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const save = vi
      .spyOn(API, "updateEpisodeSource")
      .mockResolvedValueOnce(written({ episode: 6, applied: false, needs_confirmation: true, affected_episodes: [6] }))
      .mockResolvedValueOnce(written({ episode: 6, affected_episodes: [6] }));
    const refresh = vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);

    await sourceBox();
    await userEvent.click(screen.getByRole("combobox", { name: "源文件类型" }));
    await userEvent.click(await screen.findByRole("option", { name: /^剧本/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    const dialog = await screen.findByRole("alertdialog", { name: "修改本集的源文件类型？" });
    expect(within(dialog).getByText("第 1 集：番外")).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "保存并修改类型" }));

    await waitFor(() => expect(refresh).toHaveBeenCalledWith("demo", undefined));
    expect(save.mock.calls).toEqual([
      ["demo", 6, "自带的原文", "screenplay", false],
      ["demo", 6, "自带的原文", "screenplay", true],
    ]);
  });

  it("keeps the kind change unsaved when the confirmation is declined", async () => {
    useDramaProject();
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const save = vi
      .spyOn(API, "updateEpisodeSource")
      .mockResolvedValue(written({ episode: 6, applied: false, needs_confirmation: true, affected_episodes: [6] }));

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);

    await sourceBox();
    await userEvent.click(screen.getByRole("combobox", { name: "源文件类型" }));
    await userEvent.click(await screen.findByRole("option", { name: /^剧本/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    const dialog = await screen.findByRole("alertdialog", { name: "修改本集的源文件类型？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("没有确认修改源文件类型");
    expect(save).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("combobox", { name: "源文件类型" })).toHaveTextContent("剧本");
  });

  it("集事件到达后重读原文，保留正在编辑的内容，放弃时采用 Agent 的新内容", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValueOnce("原文").mockResolvedValueOnce("Agent 改写的原文");
    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);
    fireEvent.change(await sourceBox(), { target: { value: "我的未保存修改" } });
    act(() => useAppStore.getState().invalidateEntities(["episode:6"]));
    expect(await screen.findByText("此内容已被 Agent 更新")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "本集原文" })).toHaveValue("我的未保存修改");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByRole("textbox", { name: "本集原文" })).toHaveValue("Agent 改写的原文");
  });

  it("focuses the source when the progress panel asks for the episode source", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("自带的原文");
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;

    render(<EpisodeSourceReview projectName="demo" episode={6} episodes={[OWN]} />);
    const box = await sourceBox();

    act(() => useEpisodeSurfaceStore.getState().show({ projectName: "demo", episode: 6, surface: "episode_source" }));

    expect(box).toHaveFocus();
    expect(scroll).toHaveBeenCalled();
  });

  it("focuses the extra instructions when the progress panel asks for script planning", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");
    Element.prototype.scrollIntoView = vi.fn();

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);
    await screen.findByText("text");

    act(() => useEpisodeSurfaceStore.getState().show({ projectName: "demo", episode: 1, surface: "script_plan" }));

    expect(screen.getByRole("textbox", { name: "附加指令（可选）" })).toHaveFocus();
  });

  it("shows the guide's numbered beats and hook, and leaves it out when there is neither", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");

    const { rerender } = render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    const rail = screen.getByRole("complementary", { name: "本集导览" });
    expect(within(rail).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["1主角登场", "2遭遇冲突"]);
    expect(within(rail).getByText("反派现身")).toBeInTheDocument();

    const bare = makeEpisode({ episode: 3, outline: undefined, hook: undefined });
    rerender(<EpisodeSourceReview projectName="demo" episode={3} episodes={[bare]} />);
    expect(screen.queryByRole("complementary", { name: "本集导览" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /本集导览/ })).not.toBeInTheDocument();
  });

  it("collapses the narrow-view guide and re-expands it for a different episode", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");
    const episodes = [makeEpisode(), makeEpisode({ episode: 2, outline: { story_beats: ["新的一集"] } })];

    const { rerender } = render(<EpisodeSourceReview projectName="demo" episode={1} episodes={episodes} />);

    const toggle = screen.getByRole("button", { name: /本集导览/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    rerender(<EpisodeSourceReview projectName="demo" episode={2} episodes={episodes} />);
    expect(screen.getByRole("button", { name: /本集导览/ })).toHaveAttribute("aria-expanded", "true");
  });
});
