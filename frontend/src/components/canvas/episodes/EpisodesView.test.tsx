import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { EpisodesView as EpisodesViewData, ProjectData, TaskItem } from "@/types";

import { EpisodesView } from "./EpisodesView";

const PROJECT: ProjectData = {
  title: "Demo",
  content_mode: "narration",
  style: "",
  episodes: [
    { episode: 1, title: "开端", script_file: "scripts/episode_1.json", source_origin: "whole_source" },
    { episode: 2, title: "转折", script_file: "scripts/episode_2.json", source_origin: "whole_source", ledger_status: "stale" },
    { episode: 3, title: "番外", script_file: "scripts/episode_3.json", source_origin: "own" },
  ],
  characters: {},
  scenes: {},
  props: {},
  whole_source_files: [{ source_file: "source/上卷.txt" }],
};

const VIEW: EpisodesViewData = {
  unit: "chars",
  units: 30,
  cut_units: 20,
  files: [
    {
      source_file: "source/上卷.txt",
      name: "上卷.txt",
      original_filename: "上卷.docx",
      missing: false,
      changed_outside: false,
      length: 30,
      units: 30,
      cut_units: 20,
      segments: [
        { kind: "episode", start: 0, end: 10, text: "第一集的原文。", episode: 1, gap: false, units: 10, continued: false, continues: false },
        { kind: "episode", start: 10, end: 20, text: "第二集的原文。", episode: 2, gap: false, units: 10, continued: false, continues: false },
        { kind: "unsplit", start: 20, end: 30, text: "还没分集的原文。", episode: null, gap: false, units: 10, continued: false, continues: false },
      ],
      source_kind: null,
    },
  ],
  episodes: [
    { episode: 1, origin: "whole_source", placed: true, source_file: "source/上卷.txt", end_file: "source/上卷.txt", units: 10, spoken_seconds: 3, first_sentence: "第一集的原文。", last_sentence: "第一集的原文。", source_kind: null },
    { episode: 2, origin: "whole_source", placed: true, source_file: "source/上卷.txt", end_file: "source/上卷.txt", units: 10, spoken_seconds: 3, first_sentence: "", last_sentence: "", source_kind: null },
    { episode: 3, origin: "own", placed: false, source_file: null, end_file: null, units: 8, spoken_seconds: 2, first_sentence: "", last_sentence: "", source_kind: null },
  ],
  unregistered: [],
  replan: null,
  external_changes: [],
};

/** 测试里的 IntersectionObserver：记下观察的元素，由测试手动报告哪些元素进入了视口上沿一带。 */
let observer: { callback: IntersectionObserverCallback; targets: Element[] } | null = null;

class FakeIntersectionObserver {
  constructor(callback: IntersectionObserverCallback) {
    observer = { callback, targets: [] };
  }
  observe(target: Element) {
    observer?.targets.push(target);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

function reportIntersecting(target: Element) {
  act(() =>
    observer?.callback(
      [{ target, isIntersecting: true } as unknown as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    ),
  );
}

const findManuscript = () => screen.findByRole("region", { name: "整本源文" });
const findOutline = () => screen.findByRole("navigation", { name: "集目录" });

/** 打开集目录里一集的「⋯」菜单并选一项。 */
async function chooseEpisodeAction(episodeName: string, action: string) {
  const outline = await findOutline();
  fireEvent.click(within(outline).getByRole("button", { name: `「${episodeName}」的操作` }));
  fireEvent.click(await screen.findByRole("menuitem", { name: action }));
}

/** 打开页头的「AI 规划分集」弹层。 */
async function openPlanning(label: string) {
  fireEvent.click(await screen.findByRole("button", { name: label }));
  return within(await screen.findByRole("dialog"));
}

function renderView(path = "/episodes") {
  const location = memoryLocation({ path, record: true });
  const view = render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <EpisodesView projectName="demo" />
    </Router>,
  );
  return { ...view, location };
}

describe("EpisodesView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    Element.prototype.scrollIntoView = vi.fn();
    observer = null;
    vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the whole source segmented by episode with a file bar", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const manuscript = await findManuscript();
    expect(within(manuscript).getByText("上卷.txt")).toBeInTheDocument();
    expect(within(manuscript).getByText("上传时的文件名：上卷.docx")).toBeInTheDocument();
    expect(within(manuscript).getByText("第一集的原文。")).toBeInTheDocument();
    expect(within(manuscript).getByRole("separator")).toHaveTextContent("以下内容尚未分集");
    expect(within(manuscript).getAllByText("原文已重新规划")).toHaveLength(1);
  });

  it("summarizes the episode count and the split progress in the header", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    await findManuscript();
    const header = within(screen.getByRole("heading", { name: "分集" }).closest("header") as HTMLElement);
    expect(header.getByText("3 集")).toBeInTheDocument();
    expect(header.getByText("已分集 20 / 30 字")).toBeInTheDocument();
    expect(header.getByRole("progressbar", { name: "整本源文中已分集的比例" })).toHaveAttribute("aria-valuenow", "67");
  });

  it("lists cut episodes under their file and other episodes separately", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const outline = await findOutline();
    const file = within(within(outline).getByRole("region", { name: "上卷.txt" }));
    expect(file.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("开端"),
      expect.stringContaining("转折"),
    ]);
    expect(file.getByText("之后还有 10 字未分集")).toBeInTheDocument();
    const others = within(within(outline).getByRole("region", { name: "其他集" }));
    expect(others.getByRole("link", { name: /番外/ })).toHaveAttribute("href", "/episodes/3");
    expect(others.getByText("8 字")).toBeInTheDocument();
  });

  it("scrolls the manuscript to an episode chosen in the outline", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const outline = await findOutline();
    const row = within(outline).getByRole("button", { name: /^第 2 集\s*转折/ });
    const scroll = vi.mocked(Element.prototype.scrollIntoView);
    scroll.mockClear();
    fireEvent.click(row);

    expect(row).toHaveAttribute("aria-current", "true");
    expect(scroll.mock.contexts[0]).toHaveTextContent("转折");
  });

  it("highlights the episode at the top of the manuscript as it scrolls", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const manuscript = await findManuscript();
    const outline = await findOutline();
    await waitFor(() => expect(observer?.targets).toHaveLength(2));

    reportIntersecting(within(manuscript).getByRole("article", { name: /第 2 集\s*转折/ }));
    expect(within(outline).getByRole("button", { name: /^第 2 集\s*转折/ })).toHaveAttribute("aria-current", "true");
    expect(within(outline).getByRole("button", { name: /^第 1 集\s*开端/ })).not.toHaveAttribute("aria-current");
  });

  it("opens the upload dialog from the address once and clears the parameter", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    const { location } = renderView("/episodes?upload=episode");

    await findManuscript();
    expect(screen.getByRole("dialog", { name: "上传原文" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /逐集原文/ })).toBeChecked();
    await waitFor(() => expect(location.history?.at(-1)).toBe("/episodes"));
  });

  it("opens the upload dialog again when the same address arrives a second time", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    const { location } = renderView("/episodes?upload=whole_source");

    await screen.findByRole("dialog", { name: "上传原文" });
    await waitFor(() => expect(location.history?.at(-1)).toBe("/episodes"));
    fireEvent.click(within(screen.getByRole("dialog", { name: "上传原文" })).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "上传原文" })).not.toBeInTheDocument());

    act(() => location.navigate("/episodes?upload=whole_source"));

    expect(await screen.findByRole("dialog", { name: "上传原文" })).toBeInTheDocument();
  });

  it("chooses the upload type from the upload button's menu", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: "选择上传方式" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /^逐集原文/ }));

    expect(await screen.findByRole("dialog", { name: "上传原文" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /逐集原文/ })).toBeChecked();
  });

  it("scrolls to the episode named in the address", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView("/episodes?episode=2");

    const outline = await findOutline();
    await waitFor(() =>
      expect(within(outline).getByRole("button", { name: /^第 2 集\s*转折/ })).toHaveAttribute("aria-current", "true"),
    );
    expect(vi.mocked(Element.prototype.scrollIntoView).mock.contexts).toContainEqual(
      expect.objectContaining({ textContent: expect.stringContaining("转折") }),
    );
  });

  it("invites an upload when the project has no whole source", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue({ ...VIEW, files: [], units: 0, cut_units: 0 });
    renderView();

    expect(await screen.findByText("还没有整本源文")).toBeInTheDocument();
  });

  it("reports a failed load", async () => {
    vi.spyOn(API, "getEpisodesView").mockRejectedValue(new Error("网络错误"));
    renderView();

    expect(await screen.findByText("读取分集失败：网络错误")).toBeInTheDocument();
  });

  describe("episode management", () => {
    const GAP_VIEW: EpisodesViewData = {
      ...VIEW,
      files: [
        {
          ...VIEW.files[0],
          segments: [
            { kind: "episode", start: 0, end: 10, text: "第一集的原文。", episode: 1, gap: false, units: 10, continued: false, continues: false },
            { kind: "unsplit", start: 10, end: 20, text: "删掉的那一集的原文。", episode: null, gap: true, units: 10, continued: false, continues: false },
            { kind: "episode", start: 20, end: 30, text: "第二集的原文。", episode: 2, gap: false, units: 10, continued: false, continues: false },
          ],
        },
      ],
    };

    it("plans the unsplit source left between two episodes up to its end", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(GAP_VIEW);
      const plan = vi.spyOn(API, "planEpisodes").mockResolvedValue({
        batch: { batch_id: "batch-1", members: [{ unit_id: "episode-planning", task_id: "plan-1" }] },
      });
      renderView();

      const outline = await findOutline();
      expect(within(outline).getByText("未切分的原文 10 字")).toBeInTheDocument();
      fireEvent.click(within(outline).getByRole("button", { name: "规划这段未切分的原文" }));

      await waitFor(() =>
        expect(plan).toHaveBeenCalledWith("demo", null, { source_file: "source/上卷.txt", end: 20 }),
      );
    });

    it("does not call the whole source split while a gap is left", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(GAP_VIEW);
      renderView();

      const popover = await openPlanning("已规划到结尾");
      expect(popover.getByRole("heading", { name: "已规划到整本源文结尾，中间还有未切分的原文" })).toBeInTheDocument();
      expect(popover.queryByRole("heading", { name: "整本源文已全部分集" })).not.toBeInTheDocument();
    });

    it("creates an episode after the one chosen in the outline", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      const create = vi.spyOn(API, "createEpisode").mockResolvedValue({ episode: 4 });
      useProjectsStore.setState({ refreshProject: vi.fn().mockResolvedValue(undefined) });
      renderView();

      await chooseEpisodeAction("开端", "在后面新建一集");
      const dialog = await screen.findByRole("dialog", { name: "新建一集" });
      expect(within(dialog).getByLabelText("标题")).toHaveAttribute("placeholder", "第 2 集");
      fireEvent.click(within(dialog).getByRole("button", { name: "新建" }));

      await waitFor(() =>
        expect(create).toHaveBeenCalledWith("demo", {
          after: 1,
          title: "",
          hook: "",
          source_text: null,
          source_kind: null,
        }),
      );
    });

    it("warns and closes the dialog when the project data fails to refresh after creating an episode", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
      vi.spyOn(API, "createEpisode").mockResolvedValue({ episode: 4 });
      renderView();

      await chooseEpisodeAction("开端", "在后面新建一集");
      const dialog = await screen.findByRole("dialog", { name: "新建一集" });
      fireEvent.click(within(dialog).getByRole("button", { name: "新建" }));

      await waitFor(() => expect(screen.queryByRole("dialog", { name: "新建一集" })).not.toBeInTheDocument());
      expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      });
    });

    it("offers merging, clearing and replanning only for cut episodes", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      renderView();

      await chooseEpisodeAction("转折", "打开这一集");
      const outline = await findOutline();

      fireEvent.click(within(outline).getByRole("button", { name: "「转折」的操作" }));
      // 转折是最后一个切出集：后面没有可合并或清除的集
      expect(await screen.findByRole("menuitem", { name: "与下一集合并" })).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("menuitem", { name: "清除之后的分集" })).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("menuitem", { name: "从这一集开始重新规划" })).not.toHaveAttribute("aria-disabled");
      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

      fireEvent.click(within(outline).getByRole("button", { name: "「番外」的操作" }));
      await screen.findByRole("menuitem", { name: "删除这一集" });
      expect(screen.queryByRole("menuitem", { name: "与下一集合并" })).not.toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "从这一集开始重新规划" })).not.toBeInTheDocument();
    });

    it("confirms a deletion with the loss list written by the server", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      const impact = {
        episode: 3,
        origin: "own",
        recoverable: false,
        revision: "rev-1",
        text: "删除「番外」后无法恢复，以下内容会一并删除：\n集原文（8 字）、正式脚本",
      };
      const remove = vi
        .spyOn(API, "deleteEpisode")
        .mockResolvedValueOnce({ status: "confirmation_required", impact } as never)
        .mockResolvedValueOnce({ status: "deleted", impact } as never);
      useProjectsStore.setState({ refreshProject: vi.fn().mockResolvedValue(undefined) });
      renderView();

      await chooseEpisodeAction("番外", "删除这一集");
      const dialog = await screen.findByRole("alertdialog", { name: "删除「番外」" });
      expect(dialog).toHaveTextContent("删除「番外」后无法恢复，以下内容会一并删除：");
      expect(dialog).toHaveTextContent("集原文（8 字）、正式脚本");
      fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));

      await waitFor(() => expect(remove).toHaveBeenLastCalledWith("demo", 3, "rev-1"));
    });
  });

  describe("AI planning", () => {
    function truncatedPlanning(params: Record<string, unknown>): TaskItem {
      return makeTask({
        project_name: "demo",
        task_type: "text_episode_plan",
        resource_id: "episode-planning",
        status: "failed",
        error_message: "文本模型 my-llm 的输出超出了最大输出长度，内容不完整",
        error_code: "text_output_truncated",
        error_params: params,
      });
    }

    it("plans the remaining source in one click with the instruction", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      const plan = vi.spyOn(API, "planEpisodes").mockResolvedValue({
        batch: { batch_id: "batch-1", members: [{ unit_id: "episode-planning", task_id: "plan-1" }] },
      });
      renderView();

      const popover = await openPlanning("AI 规划剩余内容");
      fireEvent.change(popover.getByLabelText("附加指令（可选，只用于这次请求，不保存）"), {
        target: { value: "按章节切" },
      });
      fireEvent.click(popover.getByRole("button", { name: "AI 规划剩余内容" }));

      await waitFor(() => expect(plan).toHaveBeenCalledWith("demo", "按章节切", null));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("shows the planning progress on the header button while planning runs", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      useTasksStore.setState({
        tasks: [makeTask({ project_name: "demo", task_type: "text_episode_plan", resource_id: "episode-planning", status: "running" })],
      });
      renderView();

      expect(await screen.findByRole("button", { name: "AI 规划中 · 67%" })).toBeInTheDocument();
    });

    it("sends a truncated custom model to its entry in settings", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      useTasksStore.setState({
        tasks: [truncatedPlanning({ provider_id: "custom-7", model: "my-llm", custom_model: true })],
      });
      const { location } = renderView();

      const popover = await openPlanning("AI 规划剩余内容");
      fireEvent.click(popover.getByRole("link", { name: "去登记最大输出长度" }));

      expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=7&model=my-llm");
    });

    it("asks to switch models when a built-in model is truncated", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      useTasksStore.setState({
        tasks: [truncatedPlanning({ provider_id: "gemini-aistudio", model: "gemini-pro", custom_model: false })],
      });
      renderView();

      const popover = await openPlanning("AI 规划剩余内容");
      expect(popover.getByText(/请在设置中换一个文本模型后再试/)).toBeInTheDocument();
      expect(popover.queryByRole("link", { name: "去登记最大输出长度" })).not.toBeInTheDocument();
    });
  });

  it("changes a drama file's source kind after confirming the started episodes it stales", async () => {
    useProjectsStore.setState({ currentProjectData: { ...PROJECT, content_mode: "drama" } });
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    vi.spyOn(API, "getEpisodesView").mockResolvedValue({
      ...VIEW,
      files: VIEW.files.map((file) => ({ ...file, source_kind: "novel" as const })),
    });
    const change = vi
      .spyOn(API, "setSourceFileKind")
      .mockResolvedValueOnce({ success: true, applied: false, needs_confirmation: true, affected_episodes: [2] })
      .mockResolvedValueOnce({ success: true, applied: true, needs_confirmation: false, affected_episodes: [2] });
    renderView();

    await userEvent.click(await screen.findByRole("combobox", { name: "上卷.txt 的源文件类型" }));
    await userEvent.click(await screen.findByRole("option", { name: /^剧本/ }));

    const dialog = await screen.findByRole("alertdialog", { name: "修改 上卷.txt 的源文件类型？" });
    expect(within(dialog).getByText("第 2 集：转折")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "修改类型" }));

    await waitFor(() => expect(useProjectsStore.getState().refreshProject).toHaveBeenCalledWith("demo", undefined));
    expect(change.mock.calls).toEqual([
      ["demo", "上卷.txt", "screenplay", false],
      ["demo", "上卷.txt", "screenplay", true],
    ]);
  });

  it("warns when the project data fails to refresh after a source kind change", async () => {
    useProjectsStore.setState({ currentProjectData: { ...PROJECT, content_mode: "drama" } });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    vi.spyOn(API, "getEpisodesView").mockResolvedValue({
      ...VIEW,
      files: VIEW.files.map((file) => ({ ...file, source_kind: "novel" as const })),
    });
    vi.spyOn(API, "setSourceFileKind").mockResolvedValue({
      success: true,
      applied: true,
      needs_confirmation: false,
      affected_episodes: [],
    });
    renderView();

    await userEvent.click(await screen.findByRole("combobox", { name: "上卷.txt 的源文件类型" }));
    await userEvent.click(await screen.findByRole("option", { name: /^剧本/ }));

    await waitFor(() => expect(useAppStore.getState().toast).toMatchObject({
      text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
      tone: "warning",
    }));
  });

  it("shows no source kind outside drama projects", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    await findManuscript();
    expect(screen.queryByRole("combobox", { name: /源文件类型/ })).not.toBeInTheDocument();
  });

  describe("replanning", () => {
    const REPLAN: NonNullable<EpisodesViewData["replan"]> = {
      id: "cand-1",
      episode: 2,
      instructions: "节奏放慢",
      complete: true,
      interrupted: null,
      stale: null,
      start: { source_file: "source/上卷.txt", offset: 10 },
      end: { source_file: "source/上卷.txt", offset: 30 },
      old_count: 1,
      new_count: 2,
      units: 20,
      average_units: 10,
      retired: [2],
      removed: [],
      needs_review: [2],
      uncovered: [],
      moved: [{ episode: 3, from: 3, to: 4 }],
      episodes: [
        { title: "新一", hook: "", source_file: "source/上卷.txt", start: 10, end: 20, units: 10, first_sentence: "第二集的原文。", last_sentence: "第二集的原文。", same_as: 2, overlaps: [2] },
        { title: "新二", hook: "", source_file: "source/上卷.txt", start: 20, end: 30, units: 10, first_sentence: "", last_sentence: "", same_as: null, overlaps: [] },
      ],
    };

    it("starts a replan from the selected cut episode after naming the started episodes", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
      vi.spyOn(API, "previewEpisodeReplan").mockResolvedValue({
        status: "preview",
        episode: 2,
        source_file: "source/上卷.txt",
        offset: 10,
        from_beginning: false,
        source_replaced: false,
        replaced: [2],
        started: [2],
      });
      const start = vi.spyOn(API, "startEpisodeReplan").mockResolvedValue({
        batch: { batch_id: "batch-1", members: [{ unit_id: "episode-planning", task_id: "plan-1" }] },
      });
      renderView();

      await chooseEpisodeAction("转折", "从这一集开始重新规划");
      const dialog = await screen.findByRole("alertdialog", { name: "从「转折」开始重新规划" });
      expect(dialog).toHaveTextContent("采纳前现有分集不变");
      expect(dialog).toHaveTextContent("其中已开始制作：转折。");
      fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "节奏放慢" } });
      const scroll = vi.mocked(Element.prototype.scrollIntoView);
      scroll.mockClear();
      fireEvent.click(within(dialog).getByRole("button", { name: "开始重新规划" }));

      await waitFor(() => expect(start).toHaveBeenCalledWith("demo", 2, "节奏放慢"));
      // 原文滚到重新规划的起点
      await waitFor(() => expect(scroll).toHaveBeenCalled());
      expect(scroll.mock.contexts.at(-1)).toHaveTextContent("转折");
    });

    it("marks the boundaries that differ between the current episodes and the new plan", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue({
        ...VIEW,
        replan: {
          ...REPLAN,
          episodes: [
            { ...REPLAN.episodes[0], start: 10, end: 14 },
            { ...REPLAN.episodes[1], start: 14, end: 30 },
          ],
        },
      });
      renderView();

      const manuscript = await findManuscript();
      // 14 落在第 2 集那一行中间，20 是第 2 集的结尾、新方案在这里不分集
      await waitFor(() => expect(manuscript.querySelectorAll("[data-replan-diff]")).toHaveLength(2));
      expect(within(manuscript).getAllByText("新旧分界不同")).toHaveLength(2);
      expect(manuscript.querySelectorAll('[data-replan-lane="new"]').length).toBeGreaterThan(0);
      expect(within(manuscript).queryByText("等待规划")).not.toBeInTheDocument();
      expect(screen.getByRole("list", { name: "新旧分法对照的图例" })).toHaveTextContent("右侧：新方案");
    });

    it("continues, adopts the finished part or discards a plan that stopped without a cut point", async () => {
      const partial = {
        ...REPLAN,
        episode: 1,
        complete: false,
        interrupted: "no_cut_point" as const,
        start: { source_file: "source/上卷.txt", offset: 0 },
        end: { source_file: "source/上卷.txt", offset: 5 },
        old_count: 2,
        new_count: 1,
        retired: [2],
        removed: [1],
        needs_review: [2],
        uncovered: [2],
        moved: [],
        episodes: [{ ...REPLAN.episodes[0], start: 0, end: 5, same_as: null, overlaps: [1] }],
      };
      vi.spyOn(API, "getEpisodesView").mockResolvedValue({ ...VIEW, replan: partial });
      const resume = vi.spyOn(API, "continueEpisodeReplan").mockResolvedValue({
        batch: { batch_id: "batch-2", members: [{ unit_id: "episode-planning", task_id: "plan-2" }] },
      });
      renderView();

      const panel = await screen.findByRole("region", { name: "新的分集方案" });
      expect(within(panel).getByRole("status")).toHaveTextContent("AI 在 上卷.txt 17% 之后的原文里找不到合适的切分点");
      expect(within(panel).getByRole("status")).toHaveTextContent("超出方案范围的集同样按被替换的集处理");
      expect(within(panel).getByText("超出方案范围").nextElementSibling).toHaveTextContent("转折");
      expect(within(panel).getByRole("button", { name: "采纳已完成的部分" })).toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "放弃新方案" })).toBeInTheDocument();

      const manuscript = screen.getByRole("region", { name: "整本源文" });
      expect(within(manuscript).getByText("等待规划")).toBeInTheDocument();
      expect(manuscript.querySelectorAll('[data-replan-lane="pending"]').length).toBeGreaterThan(0);

      fireEvent.click(within(panel).getByRole("button", { name: "继续生成" }));

      await waitFor(() => expect(resume).toHaveBeenCalledWith("demo", "cand-1"));
    });

    it("offers to register the output limit when a replan window of a custom model was truncated", async () => {
      const failed = { ...REPLAN, complete: false, interrupted: "failed" as const };
      vi.spyOn(API, "getEpisodesView").mockResolvedValue({ ...VIEW, replan: failed });
      useTasksStore.setState({
        tasks: [
          makeTask({
            project_name: "demo",
            task_type: "text_episode_plan",
            resource_id: "episode-planning",
            status: "failed",
            error_message: "文本模型 my-llm 的输出超出了最大输出长度，内容不完整",
            error_code: "text_output_truncated",
            error_params: { provider_id: "custom-7", model: "my-llm", custom_model: true },
          }),
        ],
      });
      const { location } = renderView();

      const panel = await screen.findByRole("region", { name: "新的分集方案" });
      expect(within(panel).getByRole("status")).toHaveTextContent("AI 生成出错");
      fireEvent.click(within(panel).getByRole("link", { name: "去登记最大输出长度" }));

      expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=7&model=my-llm");
    });

    it("summarizes a pending plan in place of planning and adopts it with the retired episodes deleted", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue({ ...VIEW, replan: REPLAN });
      const impact = {
        candidate: "cand-1",
        episode: 2,
        old_count: 1,
        new_count: 2,
        retired: [2],
        removed: [],
        needs_review: [2],
        uncovered: [],
        moved: [{ episode: 3, from: 3, to: 4 }],
        revision: "rev-1",
        text: "服务端成文的后果",
        delete_text: "服务端成文的丢失清单",
      };
      const adopt = vi
        .spyOn(API, "adoptEpisodeReplan")
        .mockResolvedValueOnce({ status: "confirmation_required", impact })
        .mockResolvedValueOnce({ status: "adopted", episodes: [4, 5], deleted: [2] });
      useProjectsStore.setState({ refreshProject: vi.fn().mockResolvedValue(undefined) });
      renderView();

      const panel = await screen.findByRole("region", { name: "新的分集方案" });
      expect(within(panel).getByText("1 → 2")).toBeInTheDocument();
      expect(within(panel).getByText("番外（第 3 → 4 集）")).toBeInTheDocument();
      expect(within(panel).getByText("节奏放慢")).toBeInTheDocument();
      expect(within(panel).getByText("与「转折」的原文相同")).toBeInTheDocument();
      // 新方案在时集目录让位给方案栏，页头的规划入口不可用
      expect(screen.queryByRole("navigation", { name: "集目录" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "有新的分集方案" })).toBeDisabled();
      expect(screen.queryByText(/重置/)).not.toBeInTheDocument();

      fireEvent.click(within(panel).getByRole("button", { name: "采纳新方案" }));
      const dialog = await screen.findByRole("alertdialog", { name: "采纳新的分集方案" });
      expect(dialog).toHaveTextContent("服务端成文的后果");
      expect(dialog).not.toHaveTextContent("服务端成文的丢失清单");
      fireEvent.click(within(dialog).getByRole("checkbox", { name: "一并删除" }));
      expect(dialog).toHaveTextContent("服务端成文的丢失清单");
      fireEvent.click(within(dialog).getByRole("button", { name: "采纳新方案" }));

      await waitFor(() =>
        expect(adopt).toHaveBeenLastCalledWith("demo", "cand-1", { revision: "rev-1", deleteRetired: true }),
      );
    });

    it("warns when the project data fails to refresh after adopting a plan", async () => {
      vi.spyOn(API, "getEpisodesView").mockResolvedValue({ ...VIEW, replan: REPLAN });
      vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
      const impact = {
        candidate: "cand-1",
        episode: 2,
        old_count: 1,
        new_count: 2,
        retired: [2],
        removed: [],
        needs_review: [2],
        uncovered: [],
        moved: [],
        revision: "rev-1",
        text: "服务端成文的后果",
        delete_text: "服务端成文的丢失清单",
      };
      vi.spyOn(API, "adoptEpisodeReplan")
        .mockResolvedValueOnce({ status: "confirmation_required", impact })
        .mockResolvedValueOnce({ status: "adopted", episodes: [4, 5], deleted: [] });
      renderView();

      const panel = await screen.findByRole("region", { name: "新的分集方案" });
      fireEvent.click(within(panel).getByRole("button", { name: "采纳新方案" }));
      const dialog = await screen.findByRole("alertdialog", { name: "采纳新的分集方案" });
      fireEvent.click(within(dialog).getByRole("button", { name: "采纳新方案" }));

      await waitFor(() => expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      }));
    });
  });
});
