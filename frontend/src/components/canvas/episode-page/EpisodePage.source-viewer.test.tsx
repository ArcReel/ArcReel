import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { makeNarrationSegment } from "@/test/factories";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, EpisodeScript, ProjectData } from "@/types";
import { EpisodePage } from "./EpisodePage";

vi.mock("@/components/workflow/WorkflowPanel", () => ({ WorkflowPanel: () => null }));

const OWN = (episode: number, title: string): EpisodeMeta => ({
  episode,
  title,
  script_file: `episode_${episode}.json`,
  source_origin: "own",
});

const PROJECT: ProjectData = {
  title: "测试",
  style: "",
  content_mode: "narration",
  generation_mode: "storyboard",
  characters: {},
  episodes: [OWN(1, "雨夜"), OWN(2, "天明"), { ...OWN(3, "番外"), source_origin: "none" }],
};

function script(episode: number): EpisodeScript {
  return {
    episode,
    title: "集",
    content_mode: "narration",
    novel: { title: "小说", chapter: "第一章" },
    segments: [makeNarrationSegment({ segment_id: `E${episode}S01` })],
  };
}

function page(episode: number, renderCanvas = () => <p>分镜画布</p>) {
  return (
    <LeaveGuardProvider>
      <EpisodePage
        projectName="demo"
        episode={episode}
        projectData={PROJECT}
        script={script(episode)}
        demo={false}
        onSaveTitle={vi.fn()}
        onViewUnit={vi.fn()}
        renderCanvas={renderCanvas}
      />
    </LeaveGuardProvider>
  );
}

const entry = () => screen.getByRole("button", { name: "查看原文" });
const dialog = () => screen.findByRole("dialog", { name: "本集原文" });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("EpisodePage 查看原文", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
    window.history.replaceState(null, "", "/");
  });

  it("打开只读弹层显示本集完整原文，全程不发写请求，也没有编辑控件", async () => {
    const read = vi.spyOn(API, "getSourceContent").mockResolvedValue("第一段\n第二段");
    const writeSource = vi.spyOn(API, "updateEpisodeSource");
    const writeProject = vi.spyOn(API, "updateProject");
    render(page(1));
    expect(read).not.toHaveBeenCalled();

    fireEvent.click(entry());
    const shown = within(await dialog());

    expect(await shown.findByText(/第一段\s+第二段/)).toBeInTheDocument();
    expect(read).toHaveBeenCalledWith("demo", "episode_1.txt", { signal: expect.any(AbortSignal) });
    expect(shown.getByText("雨夜")).toBeInTheDocument();
    expect(shown.queryByRole("textbox")).not.toBeInTheDocument();
    expect(shown.getAllByRole("button").map((button) => button.textContent)).toEqual(["关闭"]);

    fireEvent.click(shown.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("分镜画布")).toBeInTheDocument();
    expect(writeSource).not.toHaveBeenCalled();
    expect(writeProject).not.toHaveBeenCalled();
  });

  it("账本记为无原文的集没有入口，也不读取同名文件", () => {
    const read = vi.spyOn(API, "getSourceContent").mockResolvedValue("手放进 source/ 的同名文件");

    render(page(3));

    expect(screen.queryByRole("button", { name: "查看原文" })).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  it("切集后弹层关闭，上一集迟到的结果不落到新集上", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    vi.spyOn(API, "getSourceContent").mockImplementation((_project, filename) =>
      filename === "episode_1.txt" ? first.promise : second.promise,
    );
    const { rerender } = render(page(1));
    fireEvent.click(entry());
    await dialog();

    rerender(page(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(entry());
    const shown = within(await dialog());
    expect(shown.getByText("天明")).toBeInTheDocument();

    first.resolve("第一集的原文");
    second.resolve("第二集的原文");

    expect(await shown.findByText("第二集的原文")).toBeInTheDocument();
    expect(shown.queryByText("第一集的原文")).not.toBeInTheDocument();
  });

  it("读取中显示进度，读取失败时只显示错误提示", async () => {
    const reading = deferred<string>();
    vi.spyOn(API, "getSourceContent").mockReturnValue(reading.promise);
    render(page(1));

    fireEvent.click(entry());
    const shown = within(await dialog());

    expect(shown.getByRole("status")).toHaveTextContent("正在读取本集原文…");
    reading.reject(new Error("404"));
    expect(await shown.findByRole("alert")).toHaveTextContent("无法读取本集原文");
    expect(shown.queryByRole("status")).not.toBeInTheDocument();
  });

  it("原文为空时说明为空", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("  \n");
    render(page(1));

    fireEvent.click(entry());

    expect(await within(await dialog()).findByText("本集原文是空的")).toBeInTheDocument();
  });

  it("重新打开时读取最新原文", async () => {
    const read = vi.spyOn(API, "getSourceContent").mockResolvedValueOnce("旧原文").mockResolvedValueOnce("新原文");
    render(page(1));

    fireEvent.click(entry());
    let shown = within(await dialog());
    await shown.findByText("旧原文");
    fireEvent.click(shown.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(entry());
    shown = within(await dialog());

    expect(await shown.findByText("新原文")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });
});
