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

describe("AboutSection diagnostics download (issue #1040)", () => {
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
    await waitFor(() => expect(API.getSystemVersion).toHaveBeenCalled());
    const button = screen.getByRole("button", { name: "下载诊断日志" });

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
      // click() 触发下载后不应同步回收，否则部分浏览器下载会静默失败
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
    const anchorRef: { current: HTMLAnchorElement | null } = { current: null };
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
      const el = originalCreateElement(tagName);
      if (tagName === "a") anchorRef.current = el as HTMLAnchorElement;
      return el;
    });

    render(<AboutSection />);
    await waitFor(() => expect(API.getSystemVersion).toHaveBeenCalled());

    const button = await screen.findByRole("button", { name: "下载诊断日志" });
    await user.click(button);

    // 直接对下载锚点的 download 断言轮询：组件恒渲染的 GitHub 署名锚点会先被
    // createElement spy 捕获（download 为空），若只断言 anchorRef 非空会立即命中
    // 署名锚点而非下载锚点。断言 download 值可确保轮询到 click 后创建的下载锚点。
    await waitFor(() => expect(anchorRef.current?.download).toBe("custom-diagnostics.zip"));
  });
});
