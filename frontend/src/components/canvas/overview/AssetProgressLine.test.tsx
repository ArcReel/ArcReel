import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import type { ProjectData } from "@/types";
import { AssetProgressLine } from "./AssetProgressLine";

describe("AssetProgressLine", () => {
  it("renders available/total per asset kind and never renders unverified stale badges", () => {
    const data: ProjectData = {
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: {},
      status: {
        needs_repair: false,
        repair_reason: null,
        source_remaining: false,
        episodes_summary: { total: 0, scripted: 0, in_production: 0, completed: 0 },
        assets: {
          character: { total: 5, available: 4, stale: 3 },
          scene: { total: 2, available: 2, stale: 1 },
          prop: { total: 0, available: 0, stale: 0 },
        },
      },
    };
    render(
      <Router hook={memoryLocation({ path: "/" }).hook}>
        <AssetProgressLine data={data} />
      </Router>,
    );

    expect(screen.getByRole("link", { name: "角色 4/5" })).toHaveAttribute("href", "/characters");
    expect(screen.getByRole("link", { name: "场景 2/2" })).toHaveAttribute("href", "/scenes");
    expect(screen.queryByText(/过期/)).not.toBeInTheDocument();
  });
});
