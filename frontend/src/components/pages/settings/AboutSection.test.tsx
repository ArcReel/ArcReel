import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { BRAND } from "@/branding";
import i18n from "@/i18n";
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

    // 先用真实定时器渲染完成，避免 findByRole/waitFor 的内部轮询与 fake timers 相互卡死
    render(<AboutSection />);
    const button = await screen.findByRole("button", { name: "下载诊断日志" });

    // 切换到 fake timers 后再点击：click() 是同步调度，异步延续只在微任务
    // 中运行，可精确区分「微任务已跑完」与「宏任务（setTimeout）已触发」两个时间点
    vi.useFakeTimers();
    try {
      fireEvent.click(button);
      // 仅推进微任务队列，不推进定时器：让 handleDownloadDiagnostics 的
      // await 之后的逻辑（createObjectURL / click / setTimeout 调度）跑完
      for (let i = 0; i < 5; i++) {
        await Promise.resolve();
      }

      expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
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

    await waitFor(() => expect(screen.getByText(/faster exports/)).toBeVisible());
    expect(await screen.findByRole("link", { name: /打开 GitHub Release/ })).toHaveAttribute(
      "href",
      "https://github.com/example/ArcReel/releases/tag/v1.1.0",
    );
  });
});

describe("AboutSection documentation link", () => {
  beforeEach(() => {
    vi.spyOn(API, "getSystemVersion").mockResolvedValue(VERSION_RESPONSE);
  });

  afterEach(async () => {
    if (i18n.language !== "zh") {
      await i18n.changeLanguage("zh");
    }
  });

  it("renders the documentation external link for Chinese UI", async () => {
    render(<AboutSection />);

    const link = await screen.findByRole("link", { name: "打开使用文档" });
    expect(link).toBeVisible();
    expect(link).toHaveAttribute("href", `${BRAND.docsUrl}/`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([
    ["en", "Open Documentation"],
    ["vi", "Mở tài liệu hướng dẫn"],
    ["ru", "Открыть документацию"],
  ] as const)("points to /en/ when UI language is %s", async (lang, linkName) => {
    await i18n.changeLanguage(lang);
    render(<AboutSection />);

    const link = await screen.findByRole("link", { name: linkName });
    expect(link).toBeVisible();
    expect(link).toHaveAttribute("href", `${BRAND.docsUrl}/en/`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
