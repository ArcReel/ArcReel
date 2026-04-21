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
});
