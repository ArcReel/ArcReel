import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useScrollTarget } from "@/hooks/useScrollTarget";
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

    expect(el.classList.contains("workspace-focus-flash")).toBe(true);

    act(() => {
      vi.advanceTimersByTime(2400);
    });

    expect(el.classList.contains("workspace-focus-flash")).toBe(false);
    expect(useAppStore.getState().scrollTarget).toBeNull();
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
});
