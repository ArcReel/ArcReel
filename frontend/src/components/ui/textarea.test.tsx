import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Textarea } from "./textarea";

// jsdom 不排版：内容高度按「行数 × 每行像素」模拟，每行像素随宽度变化模拟换行。
let lineHeight = 20;
let observed: ResizeObserverCallback[] = [];

function resizeTo(width: number) {
  for (const callback of observed) {
    callback([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
  }
}

beforeEach(() => {
  lineHeight = 20;
  observed = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        observed.push(callback);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(function (
    this: HTMLTextAreaElement,
  ) {
    return this.value.split("\n").length * lineHeight;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const NINE_LINES = Array.from({ length: 9 }, (_, i) => `第 ${i + 1} 行`).join("\n");

describe("Textarea 自动撑高", () => {
  describe("浏览器不支持 field-sizing 时回退到 JS 测量", () => {
    beforeEach(() => {
      vi.stubGlobal("CSS", { supports: () => false });
    });

    it("程序写入多行文本后高度随内容撑开", () => {
      const { rerender } = render(<Textarea aria-label="正文" value="" onChange={() => {}} />);

      rerender(<Textarea aria-label="正文" value={NINE_LINES} onChange={() => {}} />);

      expect(screen.getByRole("textbox", { name: "正文" })).toHaveStyle({ height: "180px" });
    });

    it("宽度变化后按新的换行重新计算高度", () => {
      render(<Textarea aria-label="正文" value={NINE_LINES} onChange={() => {}} />);
      const textarea = screen.getByRole("textbox", { name: "正文" });

      lineHeight = 40;
      resizeTo(120);

      expect(textarea).toHaveStyle({ height: "360px" });
    });
  });

  it("浏览器支持 field-sizing 时交给 CSS 撑高", () => {
    vi.stubGlobal("CSS", { supports: () => true });

    render(<Textarea aria-label="正文" value={NINE_LINES} onChange={() => {}} />);

    expect(screen.getByRole("textbox", { name: "正文" })).not.toHaveStyle({ height: "180px" });
  });
});
