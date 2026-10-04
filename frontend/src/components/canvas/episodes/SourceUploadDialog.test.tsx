import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

import { SourceUploadDialog } from "./SourceUploadDialog";

function makeProject(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "",
    episodes: [{ episode: 1, title: "第一集", script_file: "scripts/episode_1.json" }],
    characters: {},
    scenes: {},
    props: {},
    whole_source_files: [{ source_file: "source/a.txt" }, { source_file: "source/b.txt" }],
    ...overrides,
  };
}

function txt(name: string) {
  return new File(["正文"], name, { type: "text/plain" });
}

function renderDialog(props: Partial<Parameters<typeof SourceUploadDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onUploaded = vi.fn();
  render(<SourceUploadDialog projectName="demo" onClose={onClose} onUploaded={onUploaded} {...props} />);
  return { onClose, onUploaded };
}

function listedNames() {
  const list = screen.getByRole("list", { name: "待上传的文件" });
  return within(list)
    .getAllByRole("listitem")
    .map((item) => /^\d+(.+?\.(?:txt|md))/.exec(item.textContent ?? "")?.[1]);
}

async function pickKind(label: string, kind: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: new RegExp(`^${kind}`) }));
}

describe("SourceUploadDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: makeProject() });
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("registers new whole-source files at the positions chosen in the list", async () => {
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValueOnce({ success: true, path: "source/y.txt", filename: "y.txt" })
      .mockResolvedValueOnce({ success: true, path: "source/x.txt", filename: "x.txt" });
    const { onClose, onUploaded } = renderDialog({ initialFiles: [txt("x.txt"), txt("y.txt")] });

    expect(listedNames()).toEqual(["a.txt", "b.txt", "x.txt", "y.txt"]);
    fireEvent.click(screen.getByRole("button", { name: "上移 y.txt" }));
    fireEvent.click(screen.getByRole("button", { name: "上移 y.txt" }));
    expect(listedNames()).toEqual(["a.txt", "y.txt", "b.txt", "x.txt"]);

    fireEvent.click(screen.getByRole("button", { name: "上传 2 个文件" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(upload.mock.calls.map((call) => [call[2].name, call[4]?.insertAt, call[4]?.role])).toEqual([
      ["y.txt", 1, "whole_source"],
      ["x.txt", 3, "whole_source"],
    ]);
    expect(upload.mock.calls[0][4]?.onConflict).toBe("rename");
    expect(onUploaded).toHaveBeenCalledWith({ wholeSourceFiles: ["y.txt", "x.txt"], episodes: [] });
  });

  it("names files the server saved under a different name in the completion toast", async () => {
    vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "source/a (1).txt", filename: "a (1).txt" });
    const { onClose } = renderDialog({ initialFiles: [txt("a.md")] });

    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(useAppStore.getState().toast?.text).toBe(
      "已把 1 个文件加入整本源文，与已有文件重名的另存为 a (1).txt",
    );
  });

  it("appends per-episode files to the end of the playback order in list order", async () => {
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValueOnce({ success: true, path: "source/episode_3.txt", episode: 3 })
      .mockResolvedValueOnce({ success: true, path: "source/episode_2.txt", episode: 2 });
    const { onUploaded } = renderDialog({ initialMode: "episode", initialFiles: [txt("ep-a.txt"), txt("ep-b.txt")] });

    expect(listedNames()).toEqual(["ep-a.txt", "ep-b.txt"]);
    fireEvent.click(screen.getByRole("button", { name: "下移 ep-a.txt" }));
    expect(screen.getAllByText(/将成为第 \d 集/).map((el) => el.textContent)).toEqual([
      "将成为第 2 集",
      "将成为第 3 集",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "添加为第 2–3 集" }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith({ wholeSourceFiles: [], episodes: [3, 2] }));
    expect(upload.mock.calls.map((call) => [call[2].name, call[4]?.role, call[4]?.insertAt])).toEqual([
      ["ep-b.txt", "episode", undefined],
      ["ep-a.txt", "episode", undefined],
    ]);
  });

  it("keeps episode_N file names out of the whole source", () => {
    renderDialog({ initialFiles: [txt("episode_4.txt")] });

    expect(screen.getByText(/形如 episode_N 的文件名留给集原文/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传 1 个文件" })).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: /逐集原文/ }));
    expect(screen.getByRole("button", { name: "添加为第 2 集" })).toBeEnabled();
  });

  it("stops at the first failed file and keeps the remaining files in the list", async () => {
    vi.spyOn(API, "uploadFile")
      .mockResolvedValueOnce({ success: true, path: "source/x.txt", filename: "x.txt" })
      .mockRejectedValueOnce(new Error("磁盘已满"));
    const { onClose, onUploaded } = renderDialog({ initialFiles: [txt("x.txt"), txt("y.txt"), txt("z.txt")] });

    fireEvent.click(screen.getByRole("button", { name: "上传 3 个文件" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe(
        "y.txt 上传失败：磁盘已满。前面的文件已上传，剩下的文件仍在列表里",
      ),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(onUploaded).toHaveBeenCalledWith({ wholeSourceFiles: ["x.txt"], episodes: [] });
    expect(listedNames()).toEqual(["a.txt", "b.txt", "x.txt", "y.txt", "z.txt"]);
    expect(screen.getByRole("button", { name: "上传 2 个文件" })).toBeEnabled();
  });

  it("confirms the impact list when the inserted file lands inside an episode, then uploads with its revision", async () => {
    const impact = {
      shifted: [],
      changed_with_products: [],
      changed_without_products: [1],
      retired: [],
      removed: [],
      kind_stale: [],
    };
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValueOnce({
        success: false,
        status: "confirmation_required",
        impact: { ...impact, text: "原文有变化、还没有产物：第 1 集" },
        revision: "r1",
      })
      .mockResolvedValueOnce({ success: true, status: "applied", impact, path: "source/x.txt", filename: "x.txt" });
    const { onClose } = renderDialog({ initialFiles: [txt("x.txt")] });
    fireEvent.click(screen.getByRole("button", { name: "上移 x.txt" }));

    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));
    expect(await screen.findByText("原文有变化、还没有产物：第 1 集")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "插入" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(upload.mock.calls.map((call) => [call[4]?.insertAt, call[4]?.revision])).toEqual([
      [1, undefined],
      [1, "r1"],
    ]);
  });

  it("keeps the file in the list when the insertion is not confirmed", async () => {
    vi.spyOn(API, "uploadFile").mockResolvedValue({
      success: false,
      status: "confirmation_required",
      impact: {
        shifted: [],
        changed_with_products: [1],
        changed_without_products: [],
        retired: [],
        removed: [],
        kind_stale: [],
        text: "清单",
      },
      revision: "r1",
    });
    const { onClose } = renderDialog({ initialFiles: [txt("x.txt")] });

    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));
    await screen.findByText("清单");
    fireEvent.click(within(screen.getByRole("alertdialog", { name: "插入「x.txt」" })).getByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "上传 1 个文件" })).toBeEnabled());
    expect(onClose).not.toHaveBeenCalled();
    expect(listedNames()).toEqual(["a.txt", "b.txt", "x.txt"]);
  });

  it("opens the insertion confirmation over the upload dialog with focus on Cancel", async () => {
    vi.spyOn(API, "uploadFile").mockResolvedValue({
      success: false,
      status: "confirmation_required",
      impact: {
        shifted: [],
        changed_with_products: [],
        changed_without_products: [1],
        retired: [],
        removed: [],
        kind_stale: [],
        text: "清单",
      },
      revision: "r1",
    });
    renderDialog({ initialFiles: [txt("x.txt")] });

    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));
    await screen.findByText("清单");

    const confirm = within(screen.getByRole("alertdialog", { name: "插入「x.txt」" }));
    await waitFor(() => expect(confirm.getByRole("button", { name: "取消" })).toHaveFocus());
    // 上传对话框与待上传的文件仍在确认框下面
    expect(screen.getByText("x.txt")).toBeInTheDocument();
  });

  it("skips files in unsupported formats", () => {
    renderDialog({ initialFiles: [txt("x.txt"), new File(["img"], "cover.png", { type: "image/png" })] });

    expect(screen.getByText("以下文件的格式不支持，已跳过：cover.png")).toBeInTheDocument();
    expect(listedNames()).toEqual(["a.txt", "b.txt", "x.txt"]);
  });

  it("records the source kind chosen for each file in a drama project, novel by default", async () => {
    useProjectsStore.setState({ currentProjectData: makeProject({ content_mode: "drama" }) });
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValueOnce({ success: true, path: "source/x.txt", filename: "x.txt" })
      .mockResolvedValueOnce({ success: true, path: "source/y.txt", filename: "y.txt" });
    const { onClose } = renderDialog({ initialFiles: [txt("x.txt"), txt("y.txt")] });

    await pickKind("y.txt 的源文件类型", "剧本");
    fireEvent.click(screen.getByRole("button", { name: "上传 2 个文件" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(upload.mock.calls.map((call) => [call[2].name, call[4]?.sourceKind])).toEqual([
      ["x.txt", "novel"],
      ["y.txt", "screenplay"],
    ]);
  });

  it("applies one batch source kind to every per-episode file and still allows changing one", async () => {
    useProjectsStore.setState({ currentProjectData: makeProject({ content_mode: "drama" }) });
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValueOnce({ success: true, path: "source/episode_2.txt", episode: 2 })
      .mockResolvedValueOnce({ success: true, path: "source/episode_3.txt", episode: 3 })
      .mockResolvedValueOnce({ success: true, path: "source/episode_4.txt", episode: 4 });
    const { onClose } = renderDialog({ initialMode: "episode", initialFiles: [txt("一.txt"), txt("二.txt")] });

    await pickKind("这批文件的源文件类型", "剧本");
    await pickKind("二.txt 的源文件类型", "小说");
    fireEvent.change(screen.getByLabelText("选择文件"), { target: { files: [txt("三.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "添加为第 2–4 集" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(upload.mock.calls.map((call) => [call[2].name, call[4]?.sourceKind])).toEqual([
      ["一.txt", "screenplay"],
      ["二.txt", "novel"],
      ["三.txt", "screenplay"],
    ]);
  });

  it("offers no source kind outside drama projects", async () => {
    const upload = vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "source/x.txt", filename: "x.txt" });
    const { onClose } = renderDialog({ initialFiles: [txt("x.txt")] });

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(upload.mock.calls[0][4]?.sourceKind).toBeUndefined();
  });
});
