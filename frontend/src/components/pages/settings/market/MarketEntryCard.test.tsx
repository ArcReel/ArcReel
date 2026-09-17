import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import type { MarketEntry } from "@/types";
import { MarketEntryCard } from "./MarketEntryCard";

function makeEntry(overrides: Partial<MarketEntry> = {}): MarketEntry {
  return {
    source_id: 2,
    source_display_name: "团队市场",
    type: "endpoint",
    slug: "kling-master",
    path: "endpoints/kling-master/definition.json",
    name: "kling Master",
    author: "someone",
    version: "2.1.0",
    media_type: "video",
    description: "描述",
    homepage: null,
    icon: null,
    min_app_version: null,
    min_app_version_satisfied: true,
    ...overrides,
  };
}

describe("MarketEntryCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(globalThis.URL, "createObjectURL", { writable: true, value: vi.fn(() => "blob:icon") });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", { writable: true, value: vi.fn() });
  });

  it("shows the capitalised initial without requesting an icon when the entry has none", () => {
    const getIcon = vi.spyOn(API, "getMarketEntryIcon");
    const { container } = render(
      <MarketEntryCard entry={makeEntry()} sourceName="团队市场" sourceKind="custom" appVersion="0.30.0" />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("K")).toBeInTheDocument();
    expect(getIcon).not.toHaveBeenCalled();
  });

  it("loads the icon through the proxy keyed by version and revokes it on unmount", async () => {
    const blob = new Blob(["png"], { type: "image/png" });
    const getIcon = vi.spyOn(API, "getMarketEntryIcon").mockResolvedValue(blob);
    const { container, unmount } = render(
      <MarketEntryCard
        entry={makeEntry({ icon: "endpoints/kling-master/icon.png" })}
        sourceName="团队市场"
        sourceKind="custom"
        appVersion="0.30.0"
      />,
    );

    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:icon"));
    expect(getIcon).toHaveBeenCalledWith(2, "kling-master", "2.1.0", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    const signal = getIcon.mock.calls[0][3]?.signal;

    unmount();
    expect(signal?.aborted).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:icon");
  });

  it("keeps the initial placeholder when the icon cannot be fetched", async () => {
    const getIcon = vi.spyOn(API, "getMarketEntryIcon").mockRejectedValue(new Error("502"));
    const { container } = render(
      <MarketEntryCard
        entry={makeEntry({ icon: "endpoints/kling-master/icon.svg" })}
        sourceName="团队市场"
        sourceKind="custom"
        appVersion="0.30.0"
      />,
    );

    await waitFor(() => expect(getIcon).toHaveBeenCalled());
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("K")).toBeInTheDocument();
  });

  it("dims the card and states the required app version when the current one is too old", () => {
    render(
      <MarketEntryCard
        entry={makeEntry({ min_app_version: "0.32.0", min_app_version_satisfied: false })}
        sourceName="团队市场"
        sourceKind="custom"
        appVersion="0.30.0"
      />,
    );

    const card = screen.getByRole("article", { name: "kling Master" });
    expect(card).toHaveClass("opacity-60");
    expect(screen.getByText("需要 ArcReel ≥ 0.32.0")).toHaveAttribute("title", "当前版本 0.30.0");
  });

  it("shows no version requirement when it is satisfied", () => {
    render(
      <MarketEntryCard
        entry={makeEntry({ min_app_version: "0.29.0" })}
        sourceName="团队市场"
        sourceKind="custom"
        appVersion="0.30.0"
      />,
    );

    expect(screen.getByRole("article", { name: "kling Master" })).not.toHaveClass("opacity-60");
    expect(screen.queryByText(/需要 ArcReel/)).not.toBeInTheDocument();
  });
});
