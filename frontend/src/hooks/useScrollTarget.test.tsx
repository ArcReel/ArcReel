import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";

function ScrollTargetHarness({ type }: { type: string }) {
  useScrollTarget(type);
  return null;
}

describe("useScrollTarget", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    document.body.innerHTML = "";
  });

  it("scrolls to the matching element and clears scroll target", async () => {
    const el = document.createElement("div");
    el.id = "segment-S1";
    const scrollSpy = vi.fn();
    Object.defineProperty(el, "scrollIntoView", {
      value: scrollSpy,
      writable: true,
      configurable: true,
    });
    document.body.appendChild(el);

    render(<ScrollTargetHarness type="segment" />);

    act(() => {
      useAppStore.getState().triggerScrollTo({ type: "segment", id: "S1", route: "/episodes/1" });
    });

    await waitFor(() => {
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    });
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("applies and removes highlight class after timer", async () => {
    vi.useFakeTimers();

    const el = document.createElement("div");
    el.id = "character-hero";
    Object.defineProperty(el, "scrollIntoView", {
      value: vi.fn(),
      writable: true,
      configurable: true,
    });
    document.body.appendChild(el);

    render(<ScrollTargetHarness type="character" />);
    act(() => {
      useAppStore.getState().triggerScrollTo({
        type: "character",
        id: "hero",
        route: "/characters",
        highlight: true,
      });
    });

    expect(el).toHaveClass("workspace-focus-flash");

    act(() => {
      vi.advanceTimersByTime(2400);
    });

    expect(el).not.toHaveClass("workspace-focus-flash");
    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("re-centers the target while the page it landed on is still settling its layout", async () => {
    vi.useFakeTimers();
    const callbacks: ResizeObserverCallback[] = [];
    const observed: Element[] = [];
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: ResizeObserverCallback) {
          callbacks.push(cb);
        }
        observe(target: Element) {
          observed.push(target);
        }
        unobserve() {}
        disconnect = disconnect;
      },
    );
    try {
      const scroller = document.createElement("section");
      scroller.style.overflowY = "auto";
      const el = document.createElement("article");
      el.id = "character-Hero";
      const scrollSpy = vi.fn();
      el.scrollIntoView = scrollSpy;
      scroller.appendChild(el);
      document.body.appendChild(scroller);

      render(<ScrollTargetHarness type="character" />);
      act(() => {
        useAppStore.getState().triggerScrollTo({ type: "character", id: "Hero", route: "/characters" });
      });
      expect(scrollSpy).toHaveBeenCalledTimes(1);
      expect(observed).toEqual([el, scroller]);

      // 滚动容器在定位之后才收到最终高度：重新对齐
      const notify = () => callbacks[0]([], {} as ResizeObserver);
      notify();
      expect(scrollSpy).toHaveBeenCalledTimes(2);

      // 用户自己开始滚动后不再跟随
      window.dispatchEvent(new Event("wheel"));
      expect(disconnect).toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("stops following layout changes after a short settling window", async () => {
    vi.useFakeTimers();
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect = disconnect;
      },
    );
    try {
      const el = document.createElement("div");
      el.id = "segment-S2";
      el.scrollIntoView = vi.fn();
      document.body.appendChild(el);

      render(<ScrollTargetHarness type="segment" />);
      act(() => {
        useAppStore.getState().triggerScrollTo({ type: "segment", id: "S2", route: "/episodes/1" });
      });
      expect(disconnect).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(disconnect).toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("clears target even when target element does not exist", async () => {
    vi.useFakeTimers();

    render(<ScrollTargetHarness type="scene" />);

    act(() => {
      useAppStore.getState().triggerScrollTo({ type: "scene", id: "missing", route: "/scenes" });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3050);
    });

    expect(useAppStore.getState().scrollTarget).toBeNull();
  });

  it("warns in the current language when the target never appears", async () => {
    vi.useFakeTimers();
    await i18n.changeLanguage("en");
    try {
      render(<ScrollTargetHarness type="scene" />);
      act(() => {
        useAppStore.getState().triggerScrollTo({ type: "scene", id: "missing", route: "/scenes" });
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3050);
      });

      expect(useAppStore.getState().toast).toMatchObject({
        text: "Couldn't find the content to jump to: missing",
        tone: "warning",
      });
    } finally {
      vi.useRealTimers();
      await i18n.changeLanguage("zh");
    }
  });
});
