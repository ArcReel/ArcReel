import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PrimaryButton } from "./PrimaryButton";
import { SecondaryButton } from "./SecondaryButton";
import { ModalCloseButton } from "./ModalCloseButton";

describe("PrimaryButton", () => {
  it.each(["accent", "warm", "danger"] as const)("renders tone=%s as the shared Button", (tone) => {
    render(<PrimaryButton tone={tone}>save</PrimaryButton>);
    expect(screen.getByRole("button", { name: "save" })).toHaveAttribute("data-slot", "button");
  });

  it("forwards the ref to the button element", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<PrimaryButton ref={ref}>save</PrimaryButton>);
    expect(ref.current).toBe(screen.getByRole("button", { name: "save" }));
  });

  it("does not submit the surrounding form unless asked to", () => {
    const onSubmit = vi.fn((event: SubmitEvent) => event.preventDefault());
    render(
      <form onSubmit={(event) => onSubmit(event.nativeEvent as SubmitEvent)}>
        <PrimaryButton>save</PrimaryButton>
        <PrimaryButton type="submit">submit</PrimaryButton>
      </form>,
    );
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "submit" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
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
  it("renders as the shared Button and dispatches click", () => {
    const onClick = vi.fn();
    render(<SecondaryButton onClick={onClick}>cancel</SecondaryButton>);
    const btn = screen.getByRole("button", { name: "cancel" });
    expect(btn).toHaveAttribute("data-slot", "button");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("ModalCloseButton", () => {
  it("renders with aria-label from common.close (default zh: 关闭)", () => {
    render(<ModalCloseButton onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "关闭" })).toHaveAttribute("data-slot", "button");
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
