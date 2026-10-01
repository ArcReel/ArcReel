import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesView as EpisodesViewData, ProjectData } from "@/types";

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
      length: 30,
      units: 30,
      cut_units: 20,
      segments: [
        { kind: "episode", start: 0, end: 10, text: "第一集的原文。", episode: 1, gap: false, units: 10 },
        { kind: "episode", start: 10, end: 20, text: "第二集的原文。", episode: 2, gap: false, units: 10 },
        { kind: "unsplit", start: 20, end: 30, text: "还没分集的原文。", episode: null, gap: false, units: 10 },
      ],
    },
  ],
  episodes: [
    { episode: 1, origin: "whole_source", placed: true, source_file: "source/上卷.txt", units: 10, spoken_seconds: 3, first_sentence: "第一集的原文。", last_sentence: "第一集的原文。" },
    { episode: 2, origin: "whole_source", placed: true, source_file: "source/上卷.txt", units: 10, spoken_seconds: 3, first_sentence: "", last_sentence: "" },
    { episode: 3, origin: "own", placed: false, source_file: null, units: 8, spoken_seconds: 2, first_sentence: "", last_sentence: "" },
  ],
  unregistered: [],
};

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
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows the whole source segmented by episode with a file bar", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const manuscript = await screen.findByRole("main", { name: "整本源文" });
    expect(within(manuscript).getByText("上卷.txt")).toBeInTheDocument();
    expect(within(manuscript).getByTitle("上传时的文件名：上卷.docx")).toBeInTheDocument();
    expect(within(manuscript).getByText("第一集的原文。")).toBeInTheDocument();
    expect(within(manuscript).getByRole("separator")).toHaveTextContent("以下内容尚未分集");
    expect(within(manuscript).getAllByText("原文已重新规划")).toHaveLength(1);
  });

  it("lists cut episodes under their file and other episodes separately", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView();

    const rail = await screen.findByRole("complementary", { name: "分集清单" });
    expect(within(rail).getByText("来自整本源文")).toBeInTheDocument();
    expect(within(rail).getByText(/之后还有 10 字尚未分集/)).toBeInTheDocument();
    const others = within(rail).getByText("其他集").closest("section") as HTMLElement;
    expect(within(others).getByText("番外")).toBeInTheDocument();
    expect(within(others).getByText("自带原文")).toBeInTheDocument();
  });

  it("opens the upload dialog from the address once and clears the parameter", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    const { location } = renderView("/episodes?upload=episode");

    await screen.findByRole("main", { name: "整本源文" });
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

  it("selects the episode named in the address and scrolls to it", async () => {
    vi.spyOn(API, "getEpisodesView").mockResolvedValue(VIEW);
    renderView("/episodes?episode=2");

    const manuscript = await screen.findByRole("main", { name: "整本源文" });
    const header = within(manuscript).getByRole("button", { name: /^第 2 集\s*转折/ });
    expect(header).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(header.scrollIntoView).toHaveBeenCalled());
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
});
