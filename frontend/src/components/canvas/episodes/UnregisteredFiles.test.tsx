import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, UnregisteredSourceFile } from "@/types";

import { UnregisteredFilesBanner } from "./UnregisteredFiles";

const EPISODES: EpisodeMeta[] = [
  { episode: 1, title: "开端", script_file: "scripts/episode_1.json", source_origin: "whole_source" },
  { episode: 4, title: "番外", script_file: "scripts/episode_4.json", source_origin: "none" },
];

const FILES: UnregisteredSourceFile[] = [
  { name: "旧稿.txt", size: 10, can_join_whole_source: true },
  { name: "_notes.txt", size: 5, can_join_whole_source: false },
];

function renderBanner(files: UnregisteredSourceFile[] = FILES) {
  const onChanged = vi.fn();
  render(<UnregisteredFilesBanner projectName="demo" files={files} episodes={EPISODES} onChanged={onChanged} />);
  return { onChanged };
}

function openDialog() {
  fireEvent.click(screen.getByRole("button", { name: "处理" }));
  return within(screen.getByRole("dialog", { name: "处理还没用上的文件" }));
}

describe("UnregisteredFilesBanner", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("names the files in a banner and sums up the rest", () => {
    const { unmount } = render(
      <UnregisteredFilesBanner projectName="demo" files={FILES} episodes={EPISODES} onChanged={() => {}} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("旧稿.txt、_notes.txt 还没用上");
    unmount();

    renderBanner([...FILES, { name: "草稿.md", size: 3, can_join_whole_source: true }]);
    expect(screen.getByRole("status")).toHaveTextContent("旧稿.txt、_notes.txt 等 3 个文件还没用上");
  });

  it("renders nothing when every file is in use", () => {
    renderBanner([]);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("adds a file to the whole source", async () => {
    const adopt = vi.spyOn(API, "adoptSourceFile").mockResolvedValue({ success: true, target: "whole_source" });
    const { onChanged } = renderBanner();
    const dialog = openDialog();

    fireEvent.click(within(dialog.getByRole("listitem", { name: "旧稿.txt" })).getByRole("button", { name: "加入整本源文" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(adopt).toHaveBeenCalledWith("demo", "旧稿.txt", { target: "whole_source" });
  });

  it("offers a new episode or an episode without source as the target", async () => {
    const adopt = vi.spyOn(API, "adoptSourceFile").mockResolvedValue({ success: true, target: "episode", episode: 4 });
    const { onChanged } = renderBanner();
    const item = within(openDialog().getByRole("listitem", { name: "_notes.txt" }));

    expect(item.getByRole("button", { name: "加入整本源文" })).toBeDisabled();
    fireEvent.click(item.getByRole("button", { name: "作为一集的原文" }));
    expect(screen.getAllByRole("menuitem").map((option) => option.textContent)).toEqual([
      "新的一集（排在播出顺序末尾）",
      "第 2 集：番外（无原文）",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "第 2 集：番外（无原文）" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(adopt).toHaveBeenCalledWith("demo", "_notes.txt", { target: "episode", episode: 4 });
  });

  it("deletes a file after confirmation", async () => {
    const remove = vi.spyOn(API, "deleteSourceFile").mockResolvedValue({ success: true });
    const { onChanged } = renderBanner();

    openDialog();
    fireEvent.click(screen.getByRole("button", { name: "删除 旧稿.txt" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "删除文件" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(remove).toHaveBeenCalledWith("demo", "旧稿.txt");
  });

  it("reports a failed action in the dialog without refreshing", async () => {
    vi.spyOn(API, "adoptSourceFile").mockRejectedValue(new Error("文件已被登记"));
    const { onChanged } = renderBanner();
    const dialog = openDialog();

    fireEvent.click(within(dialog.getByRole("listitem", { name: "旧稿.txt" })).getByRole("button", { name: "加入整本源文" }));

    expect(await dialog.findByRole("alert")).toHaveTextContent("处理 旧稿.txt 失败：文件已被登记");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
