import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, UnregisteredSourceFile } from "@/types";

import { UnregisteredFilesPanel } from "./UnregisteredFilesPanel";

const EPISODES: EpisodeMeta[] = [
  { episode: 1, title: "开端", script_file: "scripts/episode_1.json", source_origin: "whole_source" },
  { episode: 4, title: "番外", script_file: "scripts/episode_4.json", source_origin: "none" },
];

const FILES: UnregisteredSourceFile[] = [
  { name: "旧稿.txt", size: 10, can_join_whole_source: true },
  { name: "_notes.txt", size: 5, can_join_whole_source: false },
];

function renderPanel() {
  const onChanged = vi.fn();
  render(<UnregisteredFilesPanel projectName="demo" files={FILES} episodes={EPISODES} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "有 2 个文件还没用上" }));
  return { onChanged };
}

function fileItem(name: string) {
  return screen.getByTitle(name).closest("li") as HTMLElement;
}

describe("UnregisteredFilesPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("adds a file to the whole source", async () => {
    const adopt = vi.spyOn(API, "adoptSourceFile").mockResolvedValue({ success: true, target: "whole_source" });
    const { onChanged } = renderPanel();

    fireEvent.click(within(fileItem("旧稿.txt")).getByRole("button", { name: "加入整本源文" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(adopt).toHaveBeenCalledWith("demo", "旧稿.txt", { target: "whole_source" });
    expect(useAppStore.getState().toast?.text).toBe("已把 旧稿.txt 加入整本源文");
  });

  it("offers a new episode or an episode without source as the target", async () => {
    const adopt = vi.spyOn(API, "adoptSourceFile").mockResolvedValue({ success: true, target: "episode", episode: 4 });
    const { onChanged } = renderPanel();
    const item = fileItem("_notes.txt");

    expect(within(item).getByRole("button", { name: "加入整本源文" })).toBeDisabled();
    fireEvent.click(within(item).getByRole("button", { name: "作为一集的原文" }));
    const select = within(item).getByRole("combobox", { name: "_notes.txt 作为哪一集的原文" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "新的一集（排在播出顺序末尾）",
      "第 2 集：番外（无原文）",
    ]);
    fireEvent.change(select, { target: { value: "4" } });
    fireEvent.click(within(item).getByRole("button", { name: "确认" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(adopt).toHaveBeenCalledWith("demo", "_notes.txt", { target: "episode", episode: 4 });
  });

  it("deletes a file after confirmation", async () => {
    const remove = vi.spyOn(API, "deleteSourceFile").mockResolvedValue({ success: true });
    const { onChanged } = renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "删除 旧稿.txt" }));
    fireEvent.click(screen.getByRole("button", { name: "删除文件" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(remove).toHaveBeenCalledWith("demo", "旧稿.txt");
  });

  it("reports a failed action without refreshing", async () => {
    vi.spyOn(API, "adoptSourceFile").mockRejectedValue(new Error("文件已被登记"));
    const { onChanged } = renderPanel();

    fireEvent.click(within(fileItem("旧稿.txt")).getByRole("button", { name: "加入整本源文" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("处理 旧稿.txt 失败：文件已被登记"));
    expect(onChanged).not.toHaveBeenCalled();
  });
});
