import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModalShell } from "./ModalShell";

describe("ModalShell", () => {
  it("renders nothing when open=false", () => {
    render(
      <ModalShell open={false} onClose={() => {}}>
        <p data-testid="body">hi</p>
      </ModalShell>,
    );
    expect(screen.queryByTestId("body")).toBeNull();
  });

  it("portals dialog under document.body with role=dialog + aria-modal", () => {
    const { container } = render(
      <ModalShell open onClose={() => {}} ariaLabel="demo">
        <p data-testid="body">hi</p>
      </ModalShell>,
    );
    const body = screen.getByTestId("body");
    expect(container.contains(body)).toBe(false);
    expect(document.body.contains(body)).toBe(true);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-label", "demo");
  });

  it("binds aria-labelledby / aria-describedby when ids are provided", () => {
    render(
      <ModalShell
        open
        onClose={() => {}}
        labelledBy="t-id"
        describedBy="d-id"
      >
        <h2 id="t-id">Title</h2>
        <p id="d-id">Desc</p>
      </ModalShell>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-labelledby", "t-id");
    expect(dialog).toHaveAttribute("aria-describedby", "d-id");
    expect(dialog).not.toHaveAttribute("aria-label");
  });

  it("closes on Escape by default", () => {
    const onClose = vi.fn();
    render(
      <ModalShell open onClose={onClose} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closeOnEscape=false suppresses Esc", () => {
    const onClose = vi.fn();
    render(
      <ModalShell open closeOnEscape={false} onClose={onClose} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes when backdrop is clicked (default)", () => {
    const onClose = vi.fn();
    render(
      <ModalShell open onClose={onClose} ariaLabel="x">
        <p data-testid="body">body</p>
      </ModalShell>,
    );
    const backdrop = screen.getByTestId("modal-backdrop");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closeOnBackdrop=false disables backdrop click", () => {
    const onClose = vi.fn();
    render(
      <ModalShell open closeOnBackdrop={false} onClose={onClose} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    const backdrop = screen.getByTestId("modal-backdrop");
    fireEvent.click(backdrop);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("locks body overflow while open and restores on close", () => {
    const { rerender } = render(
      <ModalShell open={false} onClose={() => {}} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    expect(document.body.style.overflow).toBe("");
    rerender(
      <ModalShell open onClose={() => {}} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    expect(document.body.style.overflow).toBe("hidden");
    rerender(
      <ModalShell open={false} onClose={() => {}} ariaLabel="x">
        <p>body</p>
      </ModalShell>,
    );
    expect(document.body.style.overflow).toBe("");
  });

  it("focuses first focusable element inside dialog on mount (focus trap initial focus)", () => {
    render(
      <ModalShell open onClose={() => {}} ariaLabel="x">
        <button data-testid="inner-btn" type="button">
          inner
        </button>
      </ModalShell>,
    );
    expect(screen.getByTestId("inner-btn")).toBe(document.activeElement);
  });
});
