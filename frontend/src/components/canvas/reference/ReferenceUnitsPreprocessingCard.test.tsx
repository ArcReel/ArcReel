import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReferenceUnitsPreprocessingCard } from "./ReferenceUnitsPreprocessingCard";
import { API } from "@/api";

describe("ReferenceUnitsPreprocessingCard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders count label and starts collapsed", () => {
    render(
      <ReferenceUnitsPreprocessingCard projectName="proj" episode={1} unitCount={3} />,
    );
    const toggle = screen.getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.textContent).toMatch(/3/);
  });

  it("expands to show PreprocessingView on click and collapses back", async () => {
    vi.spyOn(API, "getDraftContent").mockResolvedValue("# Step 1 — Reference units");
    render(
      <ReferenceUnitsPreprocessingCard projectName="proj" episode={1} unitCount={2} />,
    );
    const toggle = screen.getByRole("button");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(API.getDraftContent).toHaveBeenCalledWith("proj", 1, 1));
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("shows no-content message when step1 draft is missing (404)", async () => {
    vi.spyOn(API, "getDraftContent").mockRejectedValue(new Error("not found"));
    render(
      <ReferenceUnitsPreprocessingCard projectName="proj" episode={5} unitCount={0} />,
    );
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(screen.getByText(/No preprocessing content|暂无预处理内容/)).toBeInTheDocument(),
    );
  });
});
