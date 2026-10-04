import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EpisodeMeta } from "@/types";
import { SidebarEpisodeList } from "./SidebarEpisodeList";

const ROW_HEIGHT = 48;

function episode(id: number, title: string, overrides: Partial<EpisodeMeta> = {}): EpisodeMeta {
  return { episode: id, title, script_file: `scripts/episode_${id}.json`, ...overrides } as EpisodeMeta;
}

// 「第二章」「第三章」切自整本源文，按源文位置排列；「番外」自带原文，放在哪里都行。
const FILES = [{ source_file: "source/novel.txt" }];
const EPISODES = [
  episode(1, "番外"),
  episode(2, "第二章", { source_origin: "whole_source", source_range: { source_file: "source/novel.txt", start: 0 } }),
  episode(3, "第三章", { source_origin: "whole_source", source_range: { source_file: "source/novel.txt", start: 500 } }),
] as EpisodeMeta[];

function renderList(onMove = vi.fn(() => Promise.resolve())) {
  render(
    <SidebarEpisodeList
      episodes={EPISODES}
      shown={EPISODES.map((ep, index) => ({ ep, position: index + 1 }))}
      wholeSourceFiles={FILES}
      activeEp={null}
      route="storyboard"
      reorderable
      onOpen={vi.fn()}
      onCreateAfter={vi.fn()}
      onMove={onMove}
      onDelete={vi.fn()}
    />,
  );
  return onMove;
}

async function pickUp(name: string) {
  screen.getByRole("button", { name: `调整「${name}」的顺序` }).focus();
  await userEvent.keyboard(" ");
  await screen.findByText(new RegExp(`已拿起「${name}」`));
}

describe("SidebarEpisodeList", () => {
  beforeEach(() => {
    // jsdom 不做布局：按条目在列表里的位置给出纵向排开的矩形，键盘拖动才有落点可算。
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const item = this.closest("li");
      const index = item ? Array.from(item.parentElement?.children ?? []).indexOf(item) : 0;
      return DOMRect.fromRect({ x: 0, y: index * ROW_HEIGHT, width: 240, height: ROW_HEIGHT });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reorders an episode with the keyboard and shows the new order while the move is saved", async () => {
    let finish = () => {};
    const onMove = renderList(vi.fn(() => new Promise<void>((resolve) => (finish = resolve))));

    await pickUp("番外");
    await userEvent.keyboard("{ArrowDown}");
    await userEvent.keyboard(" ");

    // 「番外」移到「第二章」之后
    await waitFor(() => expect(onMove).toHaveBeenCalledWith(1, 2));
    const titles = within(screen.getByRole("list")).getAllByRole("listitem").map((item) => item.textContent);
    expect(titles[0]).toContain("第二章");
    expect(titles[1]).toContain("番外");
    finish();
  });

  it("refuses to drop a cut episode out of source order", async () => {
    const onMove = renderList();

    await pickUp("第三章");
    await userEvent.keyboard("{ArrowUp}");
    await userEvent.keyboard(" ");

    expect(await screen.findByText(/「第三章」不能移到这里/)).toBeInTheDocument();
    expect(onMove).not.toHaveBeenCalled();
  });

  it("moves an episode one step from its menu", async () => {
    const user = userEvent.setup();
    const onMove = renderList();

    await user.click(screen.getByRole("button", { name: "「番外」的操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "后移" }));

    expect(onMove).toHaveBeenCalledWith(1, 2);
  });

  it("accepts no further move from the menu or the keyboard until the pending move is saved", async () => {
    const user = userEvent.setup();
    let finish = () => {};
    const onMove = renderList(vi.fn(() => new Promise<void>((resolve) => (finish = resolve))));

    await user.click(screen.getByRole("button", { name: "「番外」的操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "后移" }));
    expect(onMove).toHaveBeenCalledTimes(1);

    // 上一次移动还在保存：校验与提交仍按旧顺序，叠加的移动会算错落点
    await user.click(screen.getByRole("button", { name: "「番外」的操作" }));
    expect(await screen.findByRole("menuitem", { name: "后移" })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");

    await pickUp("番外");
    await userEvent.keyboard("{ArrowDown}");
    await userEvent.keyboard(" ");
    expect(await screen.findByText(/「番外」不能移到这里/)).toBeInTheDocument();
    expect(onMove).toHaveBeenCalledTimes(1);
    finish();
  });
});
