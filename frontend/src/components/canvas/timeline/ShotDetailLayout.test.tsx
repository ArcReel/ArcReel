import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShotSection } from "@/components/canvas/timeline/ShotDetailLayout";

/** 可控的 ResizeObserver：`resize` 把宽度推给已注册的回调。 */
function stubResizeObserver() {
  const callbacks = new Set<ResizeObserverCallback>();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(cb: ResizeObserverCallback) {
        callbacks.add(cb);
      }
      observe() {}
      disconnect() {
        callbacks.clear();
      }
      unobserve() {}
    },
  );
  return {
    resize(width: number) {
      for (const cb of callbacks) {
        cb([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
      }
    },
  };
}

describe("ShotSection", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("宽容器读完整标题", () => {
    const observer = stubResizeObserver();
    render(
      <ShotSection title="分镜图提示词" shortTitle="分镜" htmlFor="p">
        <textarea id="p" />
      </ShotSection>,
    );
    act(() => observer.resize(420));
    expect(screen.getByText("分镜图提示词")).toBeInTheDocument();
    expect(screen.queryByText("分镜")).not.toBeInTheDocument();
  });

  it("窄容器换成短标题，label 仍指向输入框", () => {
    const observer = stubResizeObserver();
    render(
      <ShotSection title="分镜图提示词" shortTitle="分镜" htmlFor="p">
        <textarea id="p" />
      </ShotSection>,
    );
    act(() => observer.resize(200));
    expect(screen.getByText("分镜")).toBeInTheDocument();
    expect(screen.getByLabelText("分镜", { selector: "textarea" })).toHaveAttribute("id", "p");
  });

  it("没给短标题时窄容器仍用完整标题", () => {
    const observer = stubResizeObserver();
    render(
      <ShotSection title="构图参考" htmlFor="p">
        <textarea id="p" />
      </ShotSection>,
    );
    act(() => observer.resize(200));
    expect(screen.getByText("构图参考")).toBeInTheDocument();
  });
});
