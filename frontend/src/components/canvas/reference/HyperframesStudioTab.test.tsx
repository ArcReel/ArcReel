import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { HyperframesWorkspaceStatus } from "@/types";
import { HyperframesStudioTab } from "./HyperframesStudioTab";

const READY: HyperframesWorkspaceStatus = {
  project_name: "demo",
  episode: 1,
  exists: true,
  workspace_path: "hyperframes/episode_01",
  composition_path: "hyperframes/episode_01/index.html",
  manifest_path: "hyperframes/episode_01/manifest.json",
  studio_status: "ready",
  studio_url: "http://localhost:12507",
};

describe("HyperframesStudioTab", () => {
  afterEach(() => vi.restoreAllMocks());

  it("embeds the complete official Studio project after starting it", async () => {
    vi.spyOn(API, "startHyperframesStudio").mockResolvedValue(READY);

    render(<HyperframesStudioTab projectName="demo" episode={1} />);

    const frame = await screen.findByTitle("HyperFrames Studio 编辑器");
    expect(frame).toHaveAttribute(
      "src",
      "http://localhost:12507/#project/episode_01",
    );
    expect(frame).toHaveAttribute("sandbox", expect.stringContaining("allow-scripts"));
    expect(screen.getByRole("link", { name: "在新窗口打开" })).toHaveAttribute(
      "href",
      "http://localhost:12507/#project/episode_01",
    );
  });

  it("shows the startup error and retries only after user action", async () => {
    const start = vi
      .spyOn(API, "startHyperframesStudio")
      .mockRejectedValueOnce(new Error("node unavailable"))
      .mockResolvedValueOnce(READY);

    render(<HyperframesStudioTab projectName="demo" episode={1} />);

    expect(await screen.findByText("node unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    expect(await screen.findByTitle("HyperFrames Studio 编辑器")).toBeInTheDocument();
  });
});
