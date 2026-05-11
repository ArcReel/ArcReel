import React, { useEffect, useRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModalShell } from "./ModalShell";

describe("ModalShell", () => {
  it("renders nothing when open=false", () => {
    render(
      <ModalShell open={false} onClose={() => {}} ariaLabel="x">
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

  it("preserves caller-set initial focus inside dialog (child useEffect runs first)", () => {
    // 模拟 AssetFormModal：子组件在更深的 useEffect 里把焦点放到表单首个输入框。
    // ModalShell 的 useFocusTrap 不应再把焦点抢回第一个可聚焦的关闭按钮。
    function Inner() {
      const inputRef = useRef<HTMLInputElement>(null);
      useEffect(() => {
        inputRef.current?.focus();
      }, []);
      return (
        <>
          <button type="button">close-first</button>
          <input ref={inputRef} data-testid="name-input" />
        </>
      );
    }
    render(
      <ModalShell open onClose={() => {}} ariaLabel="x">
        <Inner />
      </ModalShell>,
    );
    expect(screen.getByTestId("name-input")).toBe(document.activeElement);
  });

  it("restores focus to the trigger element after close (not to the now-unmounted input)", () => {
    // 真实场景：用户点 trigger button → modal 打开 → 内部表单 nameRef 抢焦
    // → 关闭 modal → 焦点应该回到原 trigger，而不是丢到 body。
    // 之前的 bug：useFocusTrap 缓存 previouslyFocused = document.activeElement，
    // 等子组件抢完焦才记录，结果记录的是 nameRef 而不是 trigger。
    function Inner() {
      const inputRef = useRef<HTMLInputElement>(null);
      useEffect(() => {
        inputRef.current?.focus();
      }, []);
      return <input ref={inputRef} data-testid="name-input" />;
    }
    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button
            type="button"
            data-testid="opener"
            onClick={() => setOpen(true)}
          >
            open
          </button>
          <ModalShell open={open} onClose={() => setOpen(false)} ariaLabel="x">
            <Inner />
          </ModalShell>
        </>
      );
    }
    render(<Harness />);

    const opener = screen.getByTestId("opener");
    opener.focus();
    expect(opener).toBe(document.activeElement);

    fireEvent.click(opener);
    // modal 打开后，子组件的 useEffect 把焦点放到 name-input
    expect(screen.getByTestId("name-input")).toBe(document.activeElement);

    // 关闭 modal，焦点应该回到 opener，而不是 body
    fireEvent.keyDown(document, { key: "Escape" });
    expect(opener).toBe(document.activeElement);
  });

  it("body overflow lock uses reference counting across stacked modals", () => {
    expect(document.body.style.overflow).toBe("");

    const { unmount: unmountA } = render(
      <ModalShell open onClose={() => {}} ariaLabel="A">
        <p>a</p>
      </ModalShell>,
    );
    expect(document.body.style.overflow).toBe("hidden");

    const { unmount: unmountB } = render(
      <ModalShell open onClose={() => {}} ariaLabel="B">
        <p>b</p>
      </ModalShell>,
    );
    expect(document.body.style.overflow).toBe("hidden");

    // 关闭先开的 A：仍有 B 打开，不能误把 body 还原成可滚动
    unmountA();
    expect(document.body.style.overflow).toBe("hidden");

    unmountB();
    expect(document.body.style.overflow).toBe("");
  });
});
