import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PrimaryButton } from "./PrimaryButton";
import { SecondaryButton } from "./SecondaryButton";
import { ModalCloseButton } from "./ModalCloseButton";

describe("PrimaryButton", () => {
  it("defaults to accent tone via data-tone attribute", () => {
    render(<PrimaryButton>save</PrimaryButton>);
    const btn = screen.getByRole("button", { name: "save" });
    expect(btn).toHaveAttribute("data-tone", "accent");
    expect(btn.className).toContain("arc-btn-primary");
  });

  it("applies data-tone='danger' for destructive actions", () => {
    render(<PrimaryButton tone="danger">replace</PrimaryButton>);
    const btn = screen.getByRole("button", { name: "replace" });
    expect(btn).toHaveAttribute("data-tone", "danger");
  });

  it("applies data-tone='warm' for warm actions (export/overwrite)", () => {
    render(<PrimaryButton tone="warm">export</PrimaryButton>);
    expect(screen.getByRole("button", { name: "export" })).toHaveAttribute(
      "data-tone",
      "warm",
    );
  });

  it("renders disabled and ignores clicks", () => {
    const onClick = vi.fn();
    render(
      <PrimaryButton disabled onClick={onClick}>
        save
      </PrimaryButton>,
    );
    const btn = screen.getByRole("button", { name: "save" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("SecondaryButton", () => {
  it("applies arc-btn-secondary class and dispatches click", () => {
    const onClick = vi.fn();
    render(<SecondaryButton onClick={onClick}>cancel</SecondaryButton>);
    const btn = screen.getByRole("button", { name: "cancel" });
    expect(btn.className).toContain("arc-btn-secondary");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("ModalCloseButton", () => {
  it("renders with aria-label from common.close (default zh: 关闭)", () => {
    render(<ModalCloseButton onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
  });

  it("allows aria-label override", () => {
    render(<ModalCloseButton ariaLabel="dismiss dialog" onClick={() => {}} />);
    expect(
      screen.getByRole("button", { name: "dismiss dialog" }),
    ).toBeInTheDocument();
  });

  it("fires onClick when clicked", () => {
    const onClick = vi.fn();
    render(<ModalCloseButton onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
