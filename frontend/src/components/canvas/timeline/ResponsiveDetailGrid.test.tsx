import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResponsiveDetailGrid } from "./ResponsiveDetailGrid";

function renderGrid(width: number, revealRightKey: string | null = null) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width } as DOMRect);
  const grid = (key: string | null) => (
    <ResponsiveDetailGrid left={<p>左栏</p>} mid={<p>中栏</p>} right={<p>右栏</p>} revealRightKey={key} />
  );
  const result = render(grid(revealRightKey));
  return { ...result, rerenderWith: (key: string | null) => result.rerender(grid(key)) };
}

describe("ResponsiveDetailGrid", () => {
  afterEach(() => vi.restoreAllMocks());

  it("单栏布局下，revealRightKey 每换一个新值就把右栏切到前台一次", () => {
    const { rerenderWith } = renderGrid(500);
    expect(screen.getByText("中栏")).toBeInTheDocument();
    expect(screen.queryByText("右栏")).not.toBeInTheDocument();

    rerenderWith("req-1");
    expect(screen.getByText("右栏")).toBeInTheDocument();

    fireEvent.click(screen.getByText("提示词"));
    rerenderWith("req-1");
    expect(screen.queryByText("右栏")).not.toBeInTheDocument();

    rerenderWith(null);
    rerenderWith("req-2");
    expect(screen.getByText("右栏")).toBeInTheDocument();
  });

  it("抽屉布局下打开着左栏抽屉时，revealRightKey 收起抽屉让右栏露出", () => {
    const { rerenderWith } = renderGrid(800);
    fireEvent.click(screen.getByText("脚本 / 对话 / 备注"));
    expect(screen.queryByText("右栏")).not.toBeInTheDocument();

    rerenderWith("req-1");
    expect(screen.getByText("右栏")).toBeInTheDocument();
  });
});
