import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PreprocessingView } from "./PreprocessingView";
import { API } from "@/api";

describe("PreprocessingView statusLabel by contentMode", () => {
  afterEach(() => vi.restoreAllMocks());

  async function renderWith(mode: "narration" | "drama" | "reference_video") {
    vi.spyOn(API, "getDraftContent").mockResolvedValue("# step1 content");
    const { container } = render(
      <PreprocessingView projectName="p" episode={1} contentMode={mode} />,
    );
    await waitFor(() => expect(API.getDraftContent).toHaveBeenCalled());
    return container;
  }

  it("renders narration statusLabel", async () => {
    await renderWith("narration");
    expect(
      screen.getByText(/Segment split complete|片段拆分已完成/),
    ).toBeInTheDocument();
  });

  it("renders drama statusLabel", async () => {
    await renderWith("drama");
    expect(
      screen.getByText(/Script normalization complete|规范化剧本已完成/),
    ).toBeInTheDocument();
  });

  it("renders reference_video statusLabel", async () => {
    await renderWith("reference_video");
    expect(
      screen.getByText(/Reference units split complete|Units 拆分已完成/),
    ).toBeInTheDocument();
  });

  // compact 模式下 status label 改为 sr-only（无可见文本），避免和上层 page header 重复；
  // markdown 容器附加 `markdown-compact` class 触发 CSS 覆写压制 h1/h2 字号。
  it("hides status chip visually under compact and tags the markdown wrapper for compact CSS", async () => {
    vi.spyOn(API, "getDraftContent").mockResolvedValue("# hi");
    const { container } = render(
      <PreprocessingView projectName="p" episode={1} contentMode="reference_video" compact />,
    );
    await waitFor(() => expect(API.getDraftContent).toHaveBeenCalled());
    // 文本存在（sr-only 不从 DOM 移除）但被 sr-only class 隐藏
    const label = screen.getByText(/Reference units split complete|Units 拆分已完成/);
    expect(label.className).toMatch(/sr-only/);
    // 绿点 status chip 不渲染
    expect(container.querySelector(".bg-emerald-500")).toBeNull();
    // markdown 容器拿到 compact marker
    expect(container.querySelector(".markdown-compact")).not.toBeNull();
  });
});
