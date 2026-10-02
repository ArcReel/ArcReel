import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesViewFile, SourceFileImpact } from "@/types";

import { SourceFileActions } from "./SourceFileActions";

const NO_IMPACT: SourceFileImpact = {
  shifted: [],
  changed_with_products: [],
  changed_without_products: [],
  retired: [],
  removed: [],
  kind_stale: [],
};

function file(withEpisode: boolean): EpisodesViewFile {
  return {
    source_file: "source/中卷.txt",
    name: "中卷.txt",
    original_filename: null,
    missing: false,
    changed_outside: false,
    length: 10,
    units: 10,
    cut_units: withEpisode ? 10 : 0,
    segments: [
      {
        kind: withEpisode ? "episode" : "unsplit",
        start: 0,
        end: 10,
        text: "第二章。夜雨。",
        episode: withEpisode ? 2 : null,
        gap: false,
        units: 10,
        continued: false,
        continues: false,
      },
    ],
    source_kind: null,
  };
}

function openMenuItem(name: string) {
  fireEvent.click(screen.getByRole("button", { name: "「中卷.txt」的操作" }));
  fireEvent.click(screen.getByRole("menuitem", { name }));
}

describe("SourceFileActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("shows the server's impact list before moving, then applies with its revision", async () => {
    const move = vi
      .spyOn(API, "moveSourceFile")
      .mockResolvedValueOnce({
        status: "confirmation_required",
        impact: { ...NO_IMPACT, shifted: [2, 3], text: "原文没变，只平移位置：第 2 集、第 3 集" },
        revision: "r1",
      })
      .mockResolvedValueOnce({ status: "applied", impact: { ...NO_IMPACT, shifted: [2, 3] } });
    render(<SourceFileActions projectName="demo" file={file(true)} index={1} total={3} />);

    openMenuItem("上移");
    expect(await screen.findByText("原文没变，只平移位置：第 2 集、第 3 集")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "移动" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("已更新「中卷.txt」"));
    expect(move.mock.calls).toEqual([
      ["demo", "中卷.txt", "up", null],
      ["demo", "中卷.txt", "up", "r1"],
    ]);
  });

  it("asks again when the impact list changed before confirming", async () => {
    const remove = vi
      .spyOn(API, "deleteWholeSourceFile")
      .mockResolvedValueOnce({
        status: "confirmation_required",
        impact: { ...NO_IMPACT, removed: [2], text: "清单一" },
        revision: "r1",
      })
      .mockResolvedValueOnce({
        status: "confirmation_required",
        impact: { ...NO_IMPACT, retired: [2], text: "清单二" },
        revision: "r2",
      });
    render(<SourceFileActions projectName="demo" file={file(true)} index={1} total={3} />);

    openMenuItem("删除文件");
    await screen.findByText("清单一");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(await screen.findByText("清单二")).toBeInTheDocument();
    expect(screen.getByText("受影响的集刚刚有变化，下面是更新后的清单。确认后才会执行。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByText("清单二")).not.toBeInTheDocument());
    expect(remove).toHaveBeenCalledTimes(2);
  });

  it("confirms locally before deleting a file that holds no episode", async () => {
    const remove = vi
      .spyOn(API, "deleteWholeSourceFile")
      .mockResolvedValue({ status: "applied", impact: NO_IMPACT });
    render(<SourceFileActions projectName="demo" file={file(false)} index={0} total={1} />);

    openMenuItem("删除文件");
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("demo", "中卷.txt", null));
    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("已删除「中卷.txt」"));
  });

  it("disables moving past either end of the file list", () => {
    render(<SourceFileActions projectName="demo" file={file(false)} index={0} total={1} />);

    fireEvent.click(screen.getByRole("button", { name: "「中卷.txt」的操作" }));

    expect(screen.getByRole("menuitem", { name: "上移" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "下移" })).toBeDisabled();
  });

  it("saves edited text through the same confirmation", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("第二章。夜雨。");
    const edit = vi.spyOn(API, "editSourceFile").mockResolvedValue({ status: "applied", impact: NO_IMPACT });
    render(<SourceFileActions projectName="demo" file={file(true)} index={0} total={1} />);

    openMenuItem("编辑原文");
    const area = await screen.findByRole("textbox", { name: "「中卷.txt」的原文" });
    fireEvent.change(area, { target: { value: "第二章。夜雨潇潇。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(edit).toHaveBeenCalledWith("demo", "中卷.txt", "第二章。夜雨潇潇。", null));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
  });

  it("keeps Tab inside the impact confirmation opened over the editor", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getSourceContent").mockResolvedValue("第二章。夜雨。");
    vi.spyOn(API, "editSourceFile").mockResolvedValue({
      status: "confirmation_required",
      impact: { ...NO_IMPACT, changed_with_products: [2], text: "原文有变化、已有产物：夜雨" },
      revision: "r1",
    });
    render(<SourceFileActions projectName="demo" file={file(true)} index={0} total={1} />);

    openMenuItem("编辑原文");
    const area = await screen.findByRole("textbox", { name: "「中卷.txt」的原文" });
    fireEvent.change(area, { target: { value: "第二章。夜雨潇潇。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("原文有变化、已有产物：夜雨");

    const confirm = within(screen.getAllByRole("dialog").at(-1)!);
    expect(confirm.getByRole("button", { name: "取消" })).toHaveFocus();
    await user.tab();
    expect(confirm.getByRole("button", { name: "保存" })).toHaveFocus();
    await user.tab();
    expect(confirm.getByRole("button", { name: "取消" })).toHaveFocus();
  });

  it("keeps only deletion available while the file was changed outside ArcReel", () => {
    render(
      <SourceFileActions projectName="demo" file={{ ...file(true), changed_outside: true }} index={1} total={3} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "「中卷.txt」的操作" }));

    for (const name of ["上移", "下移", "编辑原文", "替换为新文件"]) {
      expect(screen.getByRole("menuitem", { name })).toBeDisabled();
    }
    expect(screen.getByRole("menuitem", { name: "删除文件" })).toBeEnabled();
  });
});
