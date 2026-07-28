import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("sets role=progressbar with aria-value attributes", () => {
    const { getByRole } = render(<ProgressBar value={45} />);
    const bar = getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "45");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("honors custom min/max", () => {
    const { getByRole } = render(<ProgressBar value={7} min={0} max={10} />);
    const bar = getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "7");
    expect(bar).toHaveAttribute("aria-valuemax", "10");
  });

  it("clamps value below min to min", () => {
    const { getByRole } = render(<ProgressBar value={-5} />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("clamps value above max to max", () => {
    const { getByRole } = render(<ProgressBar value={150} />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("renders bar width as percentage of (value - min) / (max - min)", () => {
    const { getByRole } = render(<ProgressBar value={25} />);
    const bar = getByRole("progressbar");
    const fill = bar.firstElementChild as HTMLElement;
    expect(fill.style.width).toBe("25%");
  });

  it("applies aria-label when label prop provided", () => {
    const { getByRole } = render(<ProgressBar value={10} label="Upload progress" />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-label", "Upload progress");
  });

  it("merges className onto wrapper and barClassName onto fill", () => {
    const { getByRole } = render(
      <ProgressBar value={50} className="h-2" barClassName="bg-emerald-500" />,
    );
    const bar = getByRole("progressbar");
    expect(bar.className).toContain("h-2");
    expect((bar.firstElementChild as HTMLElement).className).toContain("bg-emerald-500");
  });
});
