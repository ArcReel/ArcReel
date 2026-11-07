import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { AboutSection } from "./AboutSection";
import type { GetSystemVersionResponse } from "@/types/system";

globalThis.URL.createObjectURL ??= vi.fn();
globalThis.URL.revokeObjectURL ??= vi.fn();

const VERSION_RESPONSE: GetSystemVersionResponse = {
  current: { version: "1.0.0" },
  latest: null,
  has_update: false,
  checked_at: "2026-07-13T00:00:00Z",
  update_check_error: null,
};

describe("AboutSection diagnostics download", () => {
  beforeEach(() => {
    vi.spyOn(API, "getSystemVersion").mockResolvedValue(VERSION_RESPONSE);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-diagnostics");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });

  it("defers URL.revokeObjectURL past the click, still revokes after the deferred timer fires", async () => {
    vi.spyOn(API, "downloadDiagnostics").mockResolvedValue({
      blob: new Blob(["zip-bytes"]),
      filename: "arcreel-diagnostics.zip",
    });

    // Render fully with real timers first, avoiding findByRole/waitFor internal polling deadlocking against fake timers.
    render(<AboutSection />);
    // API calls happen while version information loads; buttons render after completion.
    const button = await screen.findByRole("button", { name: "下载诊断日志" });

    // Switch to fake timers before clicks: click() schedules synchronously, asynchronous continuations run only in microtasks,
    // precisely distinguishing completed microtasks from fired setTimeout macrotasks.
    vi.useFakeTimers();
    try {
      fireEvent.click(button);
      // Advance microtasks only, not timers, letting handleDownloadDiagnostics postawait
      // createObjectURL / click / setTimeout scheduling finish.
      for (let i = 0; i < 5; i++) {
        await Promise.resolve();
      }

      expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
      // Never synchronously revoke after download click(), which silently fails in some browsers.
      expect(URL.revokeObjectURL).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(0);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-diagnostics");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the download filename unchanged", async () => {
    vi.spyOn(API, "downloadDiagnostics").mockResolvedValue({
      blob: new Blob(["zip-bytes"]),
      filename: "custom-diagnostics.zip",
    });
    const user = userEvent.setup();
    const createdAnchors: HTMLAnchorElement[] = [];
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
      const el = originalCreateElement(tagName);
      if (tagName === "a") createdAnchors.push(el as HTMLAnchorElement);
      return el;
    });

    render(<AboutSection />);
    await waitFor(() => expect(API.getSystemVersion).toHaveBeenCalled());

    const button = await screen.findByRole("button", { name: "下载诊断日志" });
    await user.click(button);

    // Search all created <a> elements for download anchors, never assume one mutable reference's last write is the target:
    // always-rendered GitHub attribution links precede download anchors. Arrays prevent future extra
    // anchors (for example changed render ordering) overwriting references and distorting tests.
    await waitFor(() => {
      const target = createdAnchors.find((a) => a.download === "custom-diagnostics.zip");
      expect(target).toBeDefined();
    });
  });
});

describe("AboutSection release notes", () => {
  it("收起最新一条发布说明，展开后显示正文与 Release 链接", async () => {
    vi.spyOn(API, "getSystemVersion").mockResolvedValue({
      ...VERSION_RESPONSE,
      latest: {
        version: "1.1.0",
        tag_name: "v1.1.0",
        name: "1.1.0",
        body: "## What's Changed\n- faster exports",
        html_url: "https://github.com/example/ArcReel/releases/tag/v1.1.0",
        published_at: "2026-07-12T08:00:00Z",
      },
      has_update: true,
    });
    const user = userEvent.setup();
    render(<AboutSection />);

    const toggle = await screen.findByRole("button", { name: /发布说明/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/faster exports/)).not.toBeInTheDocument();

    await user.click(toggle);

    // Bodies may change from plain text to Markdown; query synchronously/assert visibility without child-loading timing dependencies.
    await waitFor(() => expect(screen.getByText(/faster exports/)).toBeVisible());
    expect(await screen.findByRole("link", { name: /打开 GitHub Release/ })).toHaveAttribute(
      "href",
      "https://github.com/example/ArcReel/releases/tag/v1.1.0",
    );
  });
});
