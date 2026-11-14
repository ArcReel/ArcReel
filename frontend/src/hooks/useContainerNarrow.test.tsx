import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useContainerNarrow } from "@/hooks/useContainerNarrow";

function Probe({ threshold = 360 }: { threshold?: number }) {
  const [ref, narrow] = useContainerNarrow<HTMLDivElement>(threshold);
  return (
    <div ref={ref}>
      <output>{narrow ? "narrow" : "wide"}</output>
    </div>
  );
}

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
        // 单个实例断开时把全部回调清掉即可，用例里每个渲染只注册一次。
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

describe("useContainerNarrow", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("初始按不窄处理，避免挂载首帧闪一次短文案", () => {
    stubResizeObserver();
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("wide");
  });

  it("宽度小于阈值时为窄，达到阈值后回到宽", () => {
    const observer = stubResizeObserver();
    render(<Probe />);
    act(() => observer.resize(200));
    expect(screen.getByRole("status")).toHaveTextContent("narrow");
    act(() => observer.resize(400));
    expect(screen.getByRole("status")).toHaveTextContent("wide");
  });

  it("宽度为 0 表示尚未完成布局，不据此切换", () => {
    const observer = stubResizeObserver();
    render(<Probe />);
    act(() => observer.resize(0));
    expect(screen.getByRole("status")).toHaveTextContent("wide");
  });

  it("没有 ResizeObserver 的环境按不窄处理", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("wide");
  });
});
