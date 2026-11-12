import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MOBILE_TIER_QUERY, useMobileTier } from "@/hooks/useMobileTier";

function Probe() {
  const mobile = useMobileTier();
  return <output>{mobile ? "mobile" : "desktop"}</output>;
}

/** 可控的 matchMedia：`setMatches` 触发已注册的 change 监听。 */
function stubMatchMedia(initial: boolean) {
  const listeners = new Set<() => void>();
  let matches = initial;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === MOBILE_TIER_QUERY ? matches : false,
    media: query,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  }));
  return {
    setMatches(next: boolean) {
      matches = next;
      for (const listener of listeners) listener();
    },
  };
}

describe("useMobileTier", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("视口窄于 768px 时为移动档", () => {
    stubMatchMedia(true);
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("mobile");
  });

  it("视口达到 768px 及以上时为桌面档", () => {
    stubMatchMedia(false);
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("desktop");
  });

  it("视口跨过断点时切换档位", () => {
    const media = stubMatchMedia(false);
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("desktop");
    act(() => media.setMatches(true));
    expect(screen.getByRole("status")).toHaveTextContent("mobile");
    act(() => media.setMatches(false));
    expect(screen.getByRole("status")).toHaveTextContent("desktop");
  });

  it("没有 matchMedia 的环境按桌面档处理", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<Probe />);
    expect(screen.getByRole("status")).toHaveTextContent("desktop");
  });
});
